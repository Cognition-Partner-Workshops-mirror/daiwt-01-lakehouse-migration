SELECT CASE WHEN COUNT(*) = 0 THEN 1
    ELSE CAST('Channel calendar date is missing' AS INT) END
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
LEFT JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_calendar') AS d
    ON t.txn_date = CAST(d.calendar_date AS DATE)
WHERE t.txn_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
    AND RTRIM(t.record_status_cd) = 'A'
    AND d.calendar_date IS NULL;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')
USING DELTA AS
SELECT
    CAST(NULL AS DATE) AS txn_date,
    CAST(NULL AS STRING) AS channel,
    CAST(NULL AS INT) AS txn_count,
    CAST(NULL AS DECIMAL(18,2)) AS net_amount,
    CAST(NULL AS BIGINT) AS batch_id,
    CAST(NULL AS TIMESTAMP) AS load_ts,
    CAST(NULL AS STRING) AS src_system_cd,
    CAST(NULL AS STRING) AS record_status_cd,
    CAST(NULL AS DATE) AS effective_date,
    CAST(NULL AS DATE) AS expiry_date
WHERE FALSE;

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')
USING DELTA AS
SELECT
    CAST(t.txn_date AS DATE) AS txn_date,
    CAST(COALESCE(NULLIF(TRIM(t.channel), ''), 'ONLINE') AS STRING) AS channel,
    CAST(COUNT(*) AS INT) AS txn_count,
    CAST(COALESCE(SUM(t.amount), 0) AS DECIMAL(18,2)) AS net_amount,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST('2025-06-30 23:00:00Z' AS TIMESTAMP) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST('2024-01-01' AS DATE) AS effective_date,
    CAST('9999-12-31' AS DATE) AS expiry_date
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
INNER JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_calendar') AS d
    ON t.txn_date = CAST(d.calendar_date AS DATE)
WHERE t.txn_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
    AND RTRIM(t.record_status_cd) = 'A'
GROUP BY t.txn_date, COALESCE(NULLIF(TRIM(t.channel), ''), 'ONLINE')
UNION ALL
SELECT * FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')
WHERE txn_date < DATE '2024-01-01' OR txn_date > DATE '2025-06-30' OR txn_date IS NULL;

SELECT CASE WHEN COUNT(*) = 0 THEN 1
    ELSE CAST('Channel count or signed amount reconciliation failed' AS INT) END
FROM (
    SELECT txn_date, COALESCE(NULLIF(TRIM(channel), ''), 'ONLINE') AS channel,
        COUNT(*) AS txn_count, COALESCE(SUM(amount), 0) AS net_amount
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction')
    WHERE txn_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
        AND RTRIM(record_status_cd) = 'A'
    GROUP BY txn_date, COALESCE(NULLIF(TRIM(channel), ''), 'ONLINE')
) AS control
FULL OUTER JOIN (
    SELECT * FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')
    WHERE txn_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
) AS m ON control.txn_date = m.txn_date AND control.channel = m.channel
WHERE control.txn_date IS NULL OR m.txn_date IS NULL
    OR NOT (control.txn_count <=> m.txn_count)
    OR NOT (control.net_amount <=> m.net_amount);
