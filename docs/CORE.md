# Core interfaces

## Startup and supervision

Run `fwc-core STATE_DIRECTORY CONFIG_JSON` on Linux. Use one owner-only
directory per device. The core creates new state directories with mode 0700.
The core checks configuration on startup. On a configuration error it writes a
diagnostic to standard error, the JSON log and `status.txt` for the companion
display, then retries every two seconds. Restart the core to apply changes
after a successful boot.

```json
{
  "services": [
    {"name":"clock","command":["/usr/bin/sleep","60"],"restarts":2,"window_secs":30}
  ]
}
```

All top-level fields are optional. Each service requires a distinct nonempty
name, a command argument array with a program in position zero, and positive
32-bit integer restart and window limits. Workers inherit the working directory
and environment. Pass the bus path as a command argument when the worker needs
it. Workers run in the foreground. The core polls them every 250 ms. The first
start is free. Exits and failed launches may trigger another start as soon as
the rolling window allows. Restart budgets and counters start fresh at core
startup. Linux terminates direct worker processes when their supervisor dies.

An optional absolute `watchdog` path selects the board's Linux watchdog
device. The supervisor feeds it after each successful service check. The
device's configured timeout must exceed the longest expected check. SIGTERM
or SIGINT stops workers and writes the driver's magic-close byte. Driver and
board settings determine whether orderly close can disarm the timer.

To verify on a designated test board, configure its watchdog, start the core,
confirm its uptime exceeds the hardware timeout while feeding, then suspend
the core with SIGSTOP. Record the board, driver, timeout and observed reboot
reason from the next boot. The automated feed test uses a temporary file.

## Local bus

Connect a Unix stream socket to `STATE_DIRECTORY/bus.sock`. Each request
and reply is one UTF-8 JSON object followed by a newline. The maximum request
size including newline is 16 KiB. Keys used as topics and event IDs are
nonblank and at most 128 bytes. Send one request at a time and await its reply.
Use a separate subscriber connection for simpler reply handling.

| Request | Result |
| --- | --- |
| `{"op":"publish","topic":"activity","id":"sample-1","data":{"value":1}}` | Commits an event, then replies with `ok`, `id` and integer `seq`. |
| `{"op":"metric","topic":"power","data":75}` | Publishes a transient sample and replies with `{"ok":true}`. |
| `{"op":"subscribe","topic":"activity"}` | Selects one exact topic, replacing the previous subscription, and replies with `{"ok":true}`. |
| `{"op":"snapshot"}` | Returns `ok`, current client count, new commits, dropped metrics, queue overflows and service names, start attempts and current PIDs. |

Subscriber messages carry `kind` (event or metric), `topic` and `data`.
Events also carry `id` and `seq`. Validation and storage errors have
`ok:false` and a readable `message`. Incomplete or oversized lines
close the connection. Thirty-two clients can connect at once. Each has 32
queued frames. Under pressure the core drops metrics first. A full queue of
events or replies closes the slow connection. A blocked write times out after
five seconds. Reconnect and resubscribe to resume live updates.

## Recovery and diagnostics

Assign a stable, globally unique ID to each event. Retrying the same ID,
topic and data returns the original sequence without publishing again. A
different payload or topic for the same ID returns an error. Waiting for each
commit acknowledgment limits the producer to one uncertain event on exit.

The core holds `writer.lock` for its lifetime. All FWC writes go through its
bus. Readers open `state.sqlite` with SQLite URI option `mode=ro` and can
read `events(seq,id,topic,data)` ordered by sequence to recover missed
records. Data is JSON text. The core migrates the schema transactionally,
uses WAL with full synchronous commits, and checks the schema version before
serving clients. Keep SQLite's companion files with the database.

`core.jsonl` records timestamp (Unix seconds), kind and data. It rotates
at 1 MiB with one previous file, `core.jsonl.1`. Snapshot counters cover
the current process lifetime. To repeat the process recovery check, run
`cargo build && uv run python core/tests/faults/recovery.py`.
