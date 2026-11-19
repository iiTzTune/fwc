CREATE TABLE events (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL,
    data TEXT NOT NULL
);
PRAGMA user_version=1;
