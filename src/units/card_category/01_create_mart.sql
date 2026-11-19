CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_card_spend_category') (
    category VARCHAR(15),
    txn_count INT,
    spend DECIMAL(18, 2),
    batch_id BIGINT NOT NULL,
    load_ts TIMESTAMP NOT NULL,
    src_system_cd VARCHAR(12),
    record_status_cd CHAR(1),
    effective_date DATE,
    expiry_date DATE
)
USING DELTA;
