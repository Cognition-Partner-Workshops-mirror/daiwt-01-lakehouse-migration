-- Insert the full validated delivery and delete the previous window in one commit.
MERGE INTO IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS target
USING (
    WITH feed AS (
        SELECT
            TRIM(transaction_id) AS transaction_id,
            TRIM(account_id) AS account_id,
            TRIM(txn_date) AS txn_date,
            TRIM(amount) AS amount,
            TRIM(channel) AS channel,
            TRIM(merchant_id) AS merchant_id
        FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.transaction_feed')
        WHERE _drop = :drop
    ),
    payments AS (
        SELECT
            CASE WHEN REGEXP_LIKE(transaction_id, '^[+-]?[0-9]+$')
                THEN TRY_CAST(transaction_id AS BIGINT) END AS transaction_id,
            CASE WHEN REGEXP_LIKE(account_id, '^[+-]?[0-9]+$')
                THEN TRY_CAST(account_id AS INT) END AS account_id,
            CASE WHEN REGEXP_LIKE(txn_date, '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
                THEN TRY_CAST(txn_date AS DATE) END AS txn_date,
            TRY_CAST(REPLACE(amount, ',', '.') AS DECIMAL(18, 2)) AS amount,
            COALESCE(NULLIF(UPPER(channel), ''), 'ONLINE') AS channel,
            CASE WHEN REGEXP_LIKE(merchant_id, '^[+-]?[0-9]+$')
                THEN TRY_CAST(merchant_id AS INT) END AS merchant_id
        FROM feed
    ),
    accounts AS (
        SELECT DISTINCT CAST(account_id AS INT) AS account_id
        FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_account')
        WHERE _drop = :drop
    ),
    merchants AS (
        SELECT DISTINCT CAST(merchant_id AS INT) AS merchant_id
        FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_merchant')
        WHERE _drop = :drop
    ),
    checked AS (
        SELECT
            p.*,
            CASE
                WHEN p.transaction_id IS NULL OR p.transaction_id <= 0 THEN 'invalid transaction ID'
                WHEN COUNT(*) OVER (PARTITION BY p.transaction_id) > 1 THEN 'duplicate transaction ID'
                WHEN p.txn_date IS NULL
                    OR p.txn_date NOT BETWEEN DATE '2024-01-01' AND DATE '2025-06-30' THEN 'invalid transaction date'
                WHEN p.amount IS NULL THEN 'invalid transaction amount'
                WHEN a.account_id IS NULL THEN 'unknown account ID'
                WHEN m.merchant_id IS NULL THEN 'unknown merchant ID'
                WHEN p.channel NOT IN ('CARD', 'MOBILE', 'BRANCH', 'ONLINE') THEN 'invalid transaction channel'
            END AS rejection
        FROM payments AS p
        LEFT JOIN accounts AS a ON p.account_id = a.account_id
        LEFT JOIN merchants AS m ON p.merchant_id = m.merchant_id
    )
    SELECT
        -- The invalid cast fails in both SQL engines. The target also requires a non-null ID.
        CASE WHEN rejection IS NULL THEN transaction_id
            ELSE CAST(CONCAT('Transaction delivery rejected: ', rejection) AS BIGINT) END AS transaction_id,
        account_id,
        txn_date,
        amount,
        channel,
        merchant_id,
        CAST(20250630 AS BIGINT) AS batch_id,
        TIMESTAMP '2025-06-30 23:00:00Z' AS load_ts,
        'COREBANK' AS src_system_cd,
        'A' AS record_status_cd,
        DATE '2024-01-01' AS effective_date,
        DATE '9999-12-31' AS expiry_date,
        'NOK' AS currency_cd,
        'POSTED' AS posting_status_cd,
        'N' AS reversal_ind,
        'DOMESTIC' AS clearing_scheme_cd
    FROM checked
) AS source
ON FALSE
WHEN NOT MATCHED THEN INSERT *
WHEN NOT MATCHED BY SOURCE
    AND target.txn_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
    THEN DELETE;
