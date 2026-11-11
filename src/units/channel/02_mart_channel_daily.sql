CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily') AS
SELECT
    CAST(t.txn_date AS DATE) AS txn_date,
    CAST(COALESCE(NULLIF(TRIM(t.channel), ''), 'ONLINE') AS STRING) AS channel,
    CAST(COUNT(*) AS INT) AS txn_count,
    CAST(COALESCE(SUM(t.amount), 0) AS DECIMAL(18, 2)) AS net_amount,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST(TIMESTAMP '2025-06-30 23:00:00' AS TIMESTAMP) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST(DATE '2024-01-01' AS DATE) AS effective_date,
    CAST(DATE '9999-12-31' AS DATE) AS expiry_date
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
INNER JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_calendar') AS d
    ON t.txn_date = d.calendar_date
WHERE t.record_status_cd = 'A'
    AND t.txn_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
GROUP BY t.txn_date, COALESCE(NULLIF(TRIM(t.channel), ''), 'ONLINE');
