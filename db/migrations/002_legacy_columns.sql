ALTER TABLE payments ADD COLUMN purpose TEXT NOT NULL DEFAULT 'plan';
ALTER TABLE payments ADD COLUMN device_slot INTEGER;
ALTER TABLE devices ADD COLUMN device_name TEXT;
