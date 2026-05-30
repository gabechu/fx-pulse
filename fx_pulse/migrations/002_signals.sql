CREATE TABLE signals (
    instrument TEXT NOT NULL,
    time       TEXT NOT NULL,
    short_ma   REAL NOT NULL,
    long_ma    REAL NOT NULL,
    label      TEXT NOT NULL,
    UNIQUE(instrument, time)
);
CREATE INDEX idx_signals_instrument_time ON signals(instrument, time);
