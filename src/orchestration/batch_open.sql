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

EXECUTE IMMEDIATE 'CREATE TABLE IF NOT EXISTS `' || replace(:catalog, '`', '``') ||
  '`.`' || replace(:silver_schema, '`', '``') || '`.`etl_batch_control` (' || :control_columns || ')';

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :silver_schema || '.nightly_run_state') (
  run_id STRING, batch_date DATE, batch_id BIGINT, drop STRING, status STRING, opened_at TIMESTAMP
);
SELECT CASE WHEN count(*) <= 1 AND count_if(status = 'RUNNING') = 0
            THEN 'Batch available'
            ELSE raise_error('NIGHTLY batch already RUNNING or duplicate control rows') END
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control')
WHERE batch_date = _nightly_date AND job_name = 'NIGHTLY';

MERGE INTO IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control') AS target
USING (SELECT _nightly_date AS batch_date, 'NIGHTLY' AS job_name) AS source
ON target.batch_date = source.batch_date AND target.job_name = source.job_name
WHEN MATCHED THEN UPDATE SET status = 'OPEN', batch_id = _nightly_batch, row_count = 0,
  last_step_cd = 'batch_open', reject_count = 0
WHEN NOT MATCHED THEN INSERT (batch_date, job_name, status, row_count, batch_id,
  src_system_cd, record_status_cd, last_step_cd, window_end_date, reject_count, scheduler_group_cd)
VALUES (source.batch_date, source.job_name, 'OPEN', 0, _nightly_batch,
  'COREBANK', 'A', 'batch_open', _nightly_date, 0, 'NVB_NIGHTLY');

INSERT INTO IDENTIFIER(:catalog || '.' || :silver_schema || '.nightly_run_state')
VALUES (:run_id, _nightly_date, _nightly_batch, :drop, 'RUNNING', current_timestamp());

UPDATE IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control')
SET status = 'RUNNING'
WHERE batch_date = _nightly_date AND job_name = 'NIGHTLY';
