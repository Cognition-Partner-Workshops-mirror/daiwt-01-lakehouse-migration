CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control') AS
WITH fact_control AS (
    SELECT COUNT(*) AS payment_count, SUM(amount) AS net_amount
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction')
), mart_control AS (
    SELECT COUNT(*) AS mart_rows, SUM(txn_count) AS payment_count, SUM(net_amount) AS net_amount
    FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')
), release_rows AS (
    SELECT f.payment_count FROM fact_control AS f
    UNION ALL
    SELECT f.payment_count
    FROM fact_control AS f CROSS JOIN mart_control AS m
    WHERE NOT COALESCE(
        m.mart_rows > 0 AND f.payment_count = m.payment_count
        AND f.net_amount = m.net_amount, FALSE
    )
)
SELECT
    CAST('2025-06-30' AS DATE) AS batch_date,
    CAST('NIGHTLY' AS STRING) AS job_name,
    CAST('COMPLETE' AS STRING) AS status,
    CAST((SELECT payment_count FROM release_rows) AS BIGINT) AS row_count,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST('2025-06-30 23:00:00' AS TIMESTAMP) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST('2024-01-01' AS DATE) AS effective_date,
    CAST('9999-12-31' AS DATE) AS expiry_date,
    CAST('10_close_batch' AS STRING) AS last_step_cd,
    CAST('2024-01-01' AS DATE) AS window_start_date,
    CAST('2025-06-30' AS DATE) AS window_end_date,
    CAST(0 AS BIGINT) AS reject_count,
    CAST('NVB_NIGHTLY' AS STRING) AS scheduler_group_cd;
