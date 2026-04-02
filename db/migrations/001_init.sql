CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    marzban_username TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    telegram_id INTEGER NOT NULL,
    days INTEGER NOT NULL,
    gb INTEGER NOT NULL,
    amount_rub REAL NOT NULL,
    pay_url TEXT NOT NULL,
    status TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'plan',
    device_slot INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(provider, external_id)
);

CREATE TABLE IF NOT EXISTS devices (
    telegram_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    marzban_username TEXT NOT NULL UNIQUE,
    device_name TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(telegram_id, device_id)
);

CREATE TABLE IF NOT EXISTS referrals (
    invited_telegram_id INTEGER PRIMARY KEY,
    referrer_telegram_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    bonus_applied INTEGER NOT NULL DEFAULT 0,
    bonus_paid_at INTEGER
);

CREATE TABLE IF NOT EXISTS known_chats (
    telegram_id INTEGER PRIMARY KEY,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER,
    event_type TEXT NOT NULL,
    event_value TEXT,
    event_meta TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_type_created ON events(event_type, created_at);

CREATE TABLE IF NOT EXISTS notification_marks (
    telegram_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    mark_type TEXT NOT NULL,
    expire_ts INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(telegram_id, device_id, mark_type, expire_ts)
);

CREATE INDEX IF NOT EXISTS idx_notification_marks_created ON notification_marks(created_at);
