CREATE TABLE IF NOT EXISTS subscription_hits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER,
    marzban_username TEXT NOT NULL,
    token TEXT NOT NULL,
    client_ip TEXT,
    user_agent TEXT,
    raw_count INTEGER NOT NULL DEFAULT 0,
    unique_count INTEGER NOT NULL DEFAULT 0,
    was_deduped INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subscription_hits_created_at
    ON subscription_hits(created_at);

CREATE INDEX IF NOT EXISTS idx_subscription_hits_telegram_id
    ON subscription_hits(telegram_id, created_at);

CREATE INDEX IF NOT EXISTS idx_subscription_hits_username
    ON subscription_hits(marzban_username, created_at);
