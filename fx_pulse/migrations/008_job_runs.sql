CREATE TABLE job_runs (
    id            BIGSERIAL PRIMARY KEY,
    job_name      TEXT        NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    status        TEXT,
    rows_changed  INTEGER,
    error_text    TEXT
);
CREATE INDEX idx_job_runs_job_name_started_at ON job_runs(job_name, started_at DESC);
