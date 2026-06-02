CREATE TABLE predictions (
    predicted_at   TIMESTAMPTZ NOT NULL,
    feature_at     TIMESTAMPTZ NOT NULL,
    instrument     TEXT NOT NULL,
    model_version  TEXT NOT NULL,
    decision       TEXT NOT NULL,
    buy_proba      DOUBLE PRECISION,
    sell_proba     DOUBLE PRECISION,
    features_used  JSONB NOT NULL,
    PRIMARY KEY (instrument, predicted_at, model_version)
);
CREATE INDEX idx_predictions_predicted_at ON predictions(predicted_at);
