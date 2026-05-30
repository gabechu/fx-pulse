CREATE TABLE ticks (
    instrument TEXT NOT NULL,
    time       TEXT NOT NULL,
    bid        REAL NOT NULL,
    ask        REAL NOT NULL,
    tick_id    TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_ticks_tick_id ON ticks(tick_id);
CREATE INDEX idx_ticks_instrument_time ON ticks(instrument, time);
