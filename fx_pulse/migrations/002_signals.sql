CREATE TABLE signals (
    instrument TEXT NOT NULL,
    time       TIMESTAMPTZ NOT NULL,
    short_ma   DOUBLE PRECISION NOT NULL,
    long_ma    DOUBLE PRECISION NOT NULL,
    label      TEXT NOT NULL,
    UNIQUE(instrument, time)
);
CREATE INDEX idx_signals_instrument_time ON signals(instrument, time);
