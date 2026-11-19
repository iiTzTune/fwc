use crate::{
    drivers::{Operation, Registry},
    state::Store,
};
use anyhow::{Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    cell::RefCell,
    collections::{HashMap, VecDeque},
    rc::Rc,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::UnixStream,
    sync::Notify,
};

const LIMIT: u64 = 16384;

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Publish {
        topic: String,
        id: String,
        data: Value,
    },
    Metric {
        topic: String,
        data: Value,
    },
    Subscribe {
        topic: String,
    },
    Snapshot,
    Driver {
        request: Operation,
    },
}
impl Request {
    pub fn parse(line: &[u8]) -> Result<Self> {
        let r: Self = serde_json::from_slice(line)?;
        let key = |s: &str| !s.trim().is_empty() && s.len() <= 128;
        match &r {
            Self::Publish { id, topic, .. } => {
                ensure!(key(id) && key(topic), "invalid id or topic")
            }
            Self::Subscribe { topic } | Self::Metric { topic, .. } => {
                ensure!(key(topic), "invalid topic")
            }
            Self::Snapshot | Self::Driver { .. } => (),
        }
        Ok(r)
    }
}
#[derive(Clone)]
pub struct Frame {
    pub value: Value,
    metric: bool,
}
impl Frame {
    pub fn new(value: Value, metric: bool) -> Self {
        Self { value, metric }
    }
}
pub struct Queue {
    pub items: VecDeque<Frame>,
    pub dropped: u64,
    capacity: usize,
}
impl Queue {
    pub fn new(capacity: usize) -> Self {
        Self {
            items: VecDeque::new(),
            dropped: 0,
            capacity,
        }
    }
    pub fn push(&mut self, f: Frame) -> bool {
        if self.items.len() == self.capacity {
            if f.metric {
                self.dropped += 1;
                return true;
            }
            let Some(i) = self.items.iter().position(|x| x.metric) else {
                return false;
            };
            self.items.remove(i);
            self.dropped += 1;
        }
        self.items.push_back(f);
        true
    }
}
struct Client {
    topic: Option<String>,
    queue: Queue,
    wake: Rc<Notify>,
    closed: bool,
}
pub struct Bus {
    store: Store,
    drivers: Rc<tokio::sync::Mutex<Registry>>,
    clients: HashMap<u64, Client>,
    next: u64,
    pub services: Value,
    committed: u64,
    dropped: u64,
    overflows: u64,
}
pub type Shared = Rc<RefCell<Bus>>;
impl Bus {
    pub fn new(store: Store, drivers: Registry) -> Shared {
        Rc::new(RefCell::new(Self {
            store,
            drivers: Rc::new(tokio::sync::Mutex::new(drivers)),
            clients: HashMap::new(),
            next: 0,
            services: json!([]),
            committed: 0,
            dropped: 0,
            overflows: 0,
        }))
    }
    pub fn drivers(&self) -> Rc<tokio::sync::Mutex<Registry>> {
        self.drivers.clone()
    }
    fn send(&mut self, id: u64, f: Frame) {
        let c = self.clients.get_mut(&id).unwrap();
        if c.closed {
            return;
        }
        let prev = c.queue.dropped;
        if !c.queue.push(f) {
            c.closed = true;
            self.overflows += 1;
        }
        self.dropped += c.queue.dropped - prev;
        c.wake.notify_one();
    }
    fn distribute(&mut self, topic: &str, f: Frame) {
        let ids: Vec<_> = self
            .clients
            .iter()
            .filter(|(_, c)| c.topic.as_deref() == Some(topic))
            .map(|(id, _)| *id)
            .collect();
        for id in ids {
            self.send(id, f.clone());
        }
    }
    fn handle(&mut self, id: u64, r: Request) -> Result<Value> {
        match r {
            Request::Driver { .. } => unreachable!("driver requests run asynchronously"),
            Request::Subscribe { topic } => {
                self.clients.get_mut(&id).unwrap().topic = Some(topic);
                Ok(json!({"ok":true}))
            }
            Request::Snapshot => Ok(
                json!({"ok":true,"services":self.services,"committed":self.committed,"dropped_metrics":self.dropped,"overflows":self.overflows,"clients":self.clients.len()}),
            ),
            Request::Publish { topic, id, data } => {
                let (seq, new) = self.store.record(&id, &topic, &data)?;
                if new {
                    self.committed += 1;
                    self.distribute(
                        &topic,
                        Frame::new(
                            json!({"kind":"event","topic":topic,"seq":seq,"id":id,"data":data}),
                            false,
                        ),
                    );
                }
                Ok(json!({"ok":true,"seq":seq,"id":id}))
            }
            Request::Metric { topic, data } => {
                self.distribute(
                    &topic,
                    Frame::new(json!({"kind":"metric","topic":topic,"data":data}), true),
                );
                Ok(json!({"ok":true}))
            }
        }
    }
    pub fn accept(shared: &Shared, stream: UnixStream) {
        let mut b = shared.borrow_mut();
        if b.clients.len() >= 32 {
            return;
        }
        let id = b.next;
        b.next += 1;
        let wake = Rc::new(Notify::new());
        b.clients.insert(
            id,
            Client {
                topic: None,
                queue: Queue::new(32),
                closed: false,
                wake: wake.clone(),
            },
        );
        let bus = shared.clone();
        tokio::task::spawn_local(async move {
            let (reader, mut writer) = stream.into_split();
            let read = async {
                let mut buf = BufReader::new(reader);
                loop {
                    let mut bytes = Vec::new();
                    let n = (&mut buf)
                        .take(LIMIT + 1)
                        .read_until(b'\n', &mut bytes)
                        .await?;
                    if n == 0 || n as u64 > LIMIT || bytes.last() != Some(&b'\n') {
                        break;
                    }
                    let result = match Request::parse(&bytes) {
                        Ok(Request::Driver { request }) => {
                            let registry = bus.borrow().drivers.clone();

                            match registry.try_lock() {
                                Ok(guard) => guard
                                    .execute(request)
                                    .await
                                    .map(|data| json!({"ok":true,"data":data})),
                                Err(_) => Err(anyhow::anyhow!("driver busy")),
                            }
                        }
                        Ok(request) => bus.borrow_mut().handle(id, request),
                        Err(error) => Err(error),
                    };
                    let reply = result
                        .unwrap_or_else(|error| json!({"ok":false,"message":format!("{error:#}")}));
                    {
                        let mut b = bus.borrow_mut();
                        b.send(id, Frame::new(reply, false));
                        if b.clients[&id].closed {
                            break;
                        }
                    }
                    tokio::task::yield_now().await;
                }
                Ok::<(), std::io::Error>(())
            };
            let write = async {
                loop {
                    let next = {
                        let mut b = bus.borrow_mut();
                        let c = b.clients.get_mut(&id).unwrap();
                        if c.closed {
                            break;
                        }
                        c.queue.items.pop_front()
                    };
                    if let Some(f) = next {
                        let mut bytes = serde_json::to_vec(&f.value)?;
                        bytes.push(b'\n');
                        tokio::time::timeout(
                            std::time::Duration::from_secs(5),
                            writer.write_all(&bytes),
                        )
                        .await??;
                    } else {
                        wake.notified().await;
                    }
                }
                Ok::<(), anyhow::Error>(())
            };
            tokio::select! {_=read=>(),_=write=>()}
            bus.borrow_mut().clients.remove(&id);
        });
    }
}
