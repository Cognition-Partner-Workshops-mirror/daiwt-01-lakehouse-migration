CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_customer_segment') AS
WITH customer_activity AS (
    SELECT
        c.customer_id,
        COUNT(t.transaction_id) AS txn_count,
        coalesce(SUM(CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END), 0) AS spend,
        MAX(t.txn_date) AS last_txn_date
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') c
    LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') a
        ON c.customer_id = a.customer_id
       AND upper(rtrim(a.record_status_cd)) = 'A'
    LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') t
        ON a.account_id = t.account_id
       AND t.txn_date BETWEEN date_add(add_months(DATE '2025-06-30', -12), 1) AND DATE '2025-06-30'
       AND upper(rtrim(t.record_status_cd)) = 'A'
    WHERE upper(rtrim(c.record_status_cd)) = 'A'
    GROUP BY c.customer_id
)
SELECT
    CAST(customer_id AS INT) AS customer_id,
    CAST(txn_count AS INT) AS txn_count,
    CAST(spend AS DECIMAL(18,2)) AS spend,
    CAST(last_txn_date AS DATE) AS last_txn_date,
    CAST(CASE
        WHEN last_txn_date IS NULL OR last_txn_date < date_sub(DATE '2025-06-30', 90) THEN 'DORMANT'
        WHEN txn_count >= 50 AND spend >= 20000 THEN 'PREMIUM'
        WHEN txn_count >= 20 THEN 'ACTIVE'
        ELSE 'OCCASIONAL'
    END AS STRING) AS segment,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST(TIMESTAMP '2025-06-30 23:00:00' AS TIMESTAMP) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST(DATE '2024-01-01' AS DATE) AS effective_date,
    CAST(DATE '9999-12-31' AS DATE) AS expiry_date
FROM customer_activity;
