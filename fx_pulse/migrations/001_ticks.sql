CREATE TABLE ticks (
    instrument TEXT NOT NULL,
    time       TIMESTAMPTZ NOT NULL,
    bid        DOUBLE PRECISION NOT NULL,
    ask        DOUBLE PRECISION NOT NULL,
    tick_id    TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_ticks_tick_id ON ticks(tick_id);
CREATE INDEX idx_ticks_instrument_time ON ticks(instrument, time);
