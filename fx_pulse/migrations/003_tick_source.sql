ALTER TABLE ticks ADD COLUMN source TEXT NOT NULL DEFAULT 'live';
CREATE INDEX idx_ticks_source ON ticks(source);
