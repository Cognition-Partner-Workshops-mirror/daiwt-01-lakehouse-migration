CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control')
USING DELTA
AS
WITH fact AS (
    SELECT COUNT(*) AS txn_count, SUM(amount) AS net_amount
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction')
),
channel AS (
    SELECT SUM(txn_count) AS txn_count, SUM(net_amount) AS net_amount
    FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')
)
SELECT
    DATE '2025-06-30' AS batch_date,
    CAST('NIGHTLY' AS STRING) AS job_name,
    CAST('COMPLETE' AS STRING) AS status,
    CASE
        WHEN fact.txn_count = channel.txn_count AND fact.net_amount = channel.net_amount
        THEN CAST(fact.txn_count AS BIGINT)
        ELSE CAST(CONCAT('close_batch reconciliation failed; fact count: ', CAST(fact.txn_count AS STRING)) AS BIGINT)
    END AS row_count,
    CAST(20250630 AS BIGINT) AS batch_id,
    TIMESTAMP '2025-06-30 23:00:00+00:00' AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    DATE '2024-01-01' AS effective_date,
    DATE '9999-12-31' AS expiry_date,
    CAST('10_close_batch' AS STRING) AS last_step_cd,
    DATE '2024-01-01' AS window_start_date,
    DATE '2025-06-30' AS window_end_date,
    CAST(0 AS BIGINT) AS reject_count,
    CAST('NVB_NIGHTLY' AS STRING) AS scheduler_group_cd
FROM fact
CROSS JOIN channel;
