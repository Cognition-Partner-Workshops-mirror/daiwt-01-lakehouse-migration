SET ANSI_MODE = true;
DECLARE OR REPLACE VARIABLE _nightly_date DATE;
DECLARE OR REPLACE VARIABLE _nightly_batch BIGINT;
SET VAR (_nightly_date, _nightly_batch) = (
  SELECT delivery.business_date, delivery.batch_id
  FROM (SELECT element_at(from_json(:manifest_drops,
    'MAP<STRING, STRUCT<batch_id: BIGINT, business_date: DATE, counts: MAP<STRING, BIGINT>>>'), :drop) AS delivery)
);
SELECT CASE WHEN _nightly_date IS NOT NULL AND _nightly_batch IS NOT NULL
            THEN 'Valid captured drop'
            ELSE raise_error(concat('Unknown captured drop: ', coalesce(:drop, '<NULL>'))) END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :silver_schema || '.nightly_control_report') (
  run_id STRING, drop STRING, batch_date DATE, batch_id BIGINT, report_name STRING,
  payment_count BIGINT, net_amount DECIMAL(38,2), first_date DATE, last_date DATE,
  status STRING, row_count BIGINT, last_step_cd STRING, created_at TIMESTAMP
);
DELETE FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.nightly_control_report')
WHERE created_at < current_timestamp() - INTERVAL 400 DAYS OR run_id = :run_id;
INSERT INTO IDENTIFIER(:catalog || '.' || :silver_schema || '.nightly_control_report')
SELECT :run_id, :drop, _nightly_date, _nightly_batch, '02_transactions',
       count(*), sum(amount), min(txn_date), max(txn_date),
       CAST(NULL AS STRING), CAST(NULL AS BIGINT), CAST(NULL AS STRING), current_timestamp()
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction')
UNION ALL
SELECT :run_id, :drop, batch_date, batch_id, 'nightly_release',
       CAST(NULL AS BIGINT), CAST(NULL AS DECIMAL(38,2)), CAST(NULL AS DATE), CAST(NULL AS DATE),
       status, row_count, last_step_cd, current_timestamp()
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control')
WHERE batch_date = _nightly_date AND job_name = 'NIGHTLY';
