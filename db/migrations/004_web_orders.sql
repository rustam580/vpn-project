CREATE TABLE IF NOT EXISTS web_orders (
    order_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    plan_key TEXT NOT NULL,
    days INTEGER NOT NULL,
    gb INTEGER NOT NULL,
    amount_rub REAL NOT NULL,
    customer_contact TEXT,
    marzban_username TEXT,
    pay_url TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_orders_status_created
    ON web_orders(status, created_at);

CREATE INDEX IF NOT EXISTS idx_web_orders_username
    ON web_orders(marzban_username);
