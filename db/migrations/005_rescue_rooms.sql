CREATE TABLE IF NOT EXISTS rescue_rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL UNIQUE,
    room_url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'free',
    assigned_tg_id INTEGER,
    session_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    fail_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_ok_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_rescue_rooms_status_updated
    ON rescue_rooms(status, updated_at);

CREATE INDEX IF NOT EXISTS idx_rescue_rooms_assigned_tg
    ON rescue_rooms(assigned_tg_id);
