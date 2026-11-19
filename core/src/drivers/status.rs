use super::Registry;
use std::{
    fs::File,
    io::{ErrorKind, Read},
    path::PathBuf,
    rc::Rc,
    time::Duration,
};
use tokio::{sync::Mutex, time::sleep};

/// The capture worker owns the diagnostic file. Only refresh on a change.
pub async fn watch(registry: Rc<Mutex<Registry>>, path: PathBuf, mut displayed: Option<String>) {
    loop {
        let message = match File::open(&path) {
            Ok(file) => {
                let mut bytes = Vec::new();
                match file.take(2048).read_to_end(&mut bytes) {
                    Ok(_) => Some(String::from_utf8_lossy(&bytes).trim().to_string()),
                    Err(error) => Some(format!("Could not read capture status: {error}")),
                }
            }
            Err(error) if error.kind() == ErrorKind::NotFound => None,
            Err(error) => Some(format!("Could not open capture status: {error}")),
        };
        if displayed != message {
            let result = registry
                .lock()
                .await
                .show_status(message.as_deref().unwrap_or("Ready"))
                .await;
            match result {
                Ok(()) => displayed = message,
                Err(error) => {
                    eprintln!(
                        "{}",
                        serde_json::json!({"kind":"display_error","message":format!("{error:#}")})
                    );
                    sleep(Duration::from_secs(30)).await;
                    continue;
                }
            }
        }
        sleep(Duration::from_secs(2)).await;
    }
}
