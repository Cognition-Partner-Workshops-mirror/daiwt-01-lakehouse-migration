CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') (
    transaction_id BIGINT NOT NULL,
    account_id INT,
    txn_date DATE,
    amount DECIMAL(18, 2),
    channel VARCHAR(10),
    merchant_id INT,
    batch_id BIGINT NOT NULL,
    load_ts TIMESTAMP NOT NULL,
    src_system_cd VARCHAR(12),
    record_status_cd CHAR(1),
    effective_date DATE,
    expiry_date DATE,
    currency_cd CHAR(3),
    posting_status_cd VARCHAR(10),
    reversal_ind CHAR(1),
    clearing_scheme_cd VARCHAR(12)
)
USING DELTA;
