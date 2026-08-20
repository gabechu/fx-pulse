-- Hybrid decision heads: rule-driven live decisions with provenance, plus the
-- ML head's raw score and shadow signal for side-by-side evaluation.
ALTER TABLE predictions
    ADD COLUMN buy_raw_score    DOUBLE PRECISION,
    ADD COLUMN ml_buy_threshold DOUBLE PRECISION,
    ADD COLUMN ml_buy_signal    BOOLEAN,
    ADD COLUMN decision_source  TEXT;