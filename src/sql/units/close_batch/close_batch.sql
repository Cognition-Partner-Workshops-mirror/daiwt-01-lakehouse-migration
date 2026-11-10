-- legacy/bteq/10_close_batch.btq: reconcile fct_transaction with mart_channel_daily,
-- then close the NIGHTLY batch. Unlisted columns take the DDL defaults of
-- legacy/ddl/nvb_edw.etl_batch_control.sql.
SET ANSI_MODE = true;
DECLARE OR REPLACE VARIABLE _close_date DATE;
DECLARE OR REPLACE VARIABLE _close_batch BIGINT;
DECLARE OR REPLACE VARIABLE _close_slices BIGINT;
SET VAR (_close_date, _close_batch, _close_slices) = (
  SELECT max(_business_date), max(_batch_id), count(DISTINCT _business_date, _batch_id)
  FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.stg_transaction_feed')
  WHERE _drop = IDENTIFIER(:catalog || '.' || :silver_schema || '.resolve_drop')(:drop)
);
SELECT CASE WHEN _close_slices = 1 THEN 'Single delivery slice'
            ELSE raise_error(concat('Delivery date/batch not single-valued for drop ', coalesce(:drop, '<NULL>'))) END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control') (
  batch_date DATE, job_name STRING, status STRING, row_count BIGINT, batch_id BIGINT,
  src_system_cd STRING, record_status_cd STRING, effective_date DATE, expiry_date DATE,
  last_step_cd STRING, window_start_date DATE, window_end_date DATE, reject_count BIGINT,
  scheduler_group_cd STRING
);

SELECT CASE WHEN count(*) <= 1 THEN 'Batch row unique'
            ELSE raise_error('Duplicate NIGHTLY batch-control rows') END
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control')
WHERE batch_date = _close_date AND job_name = 'NIGHTLY';

DECLARE OR REPLACE VARIABLE _close_count BIGINT;
DECLARE OR REPLACE VARIABLE _close_amount DECIMAL(38,2);
SET VAR (_close_count, _close_amount) = (
  SELECT count(*), sum(amount)
  FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction')
);

SELECT CASE WHEN coalesce(_close_count <> m.payment_count OR _close_amount <> m.net_amount
                          OR m.payment_count IS NULL, false)
            THEN raise_error('Control failure: fct_transaction does not reconcile with mart_channel_daily')
            ELSE 'Transactions reconcile with channel mart' END
FROM (
  SELECT sum(txn_count) AS payment_count, sum(net_amount) AS net_amount
  FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')
) AS m;

MERGE INTO IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control') AS target
USING (SELECT _close_date AS batch_date, 'NIGHTLY' AS job_name) AS source
ON target.batch_date = source.batch_date AND target.job_name = source.job_name
WHEN MATCHED THEN UPDATE SET
  status = 'COMPLETE', last_step_cd = '10_close_batch', row_count = _close_count,
  batch_id = coalesce(target.batch_id, _close_batch),
  src_system_cd = coalesce(target.src_system_cd, 'COREBANK'),
  record_status_cd = coalesce(target.record_status_cd, 'A'),
  effective_date = coalesce(target.effective_date, CAST(:window_start AS DATE)),
  expiry_date = coalesce(target.expiry_date, DATE '9999-12-31'),
  window_start_date = coalesce(target.window_start_date, CAST(:window_start AS DATE)),
  window_end_date = coalesce(target.window_end_date, _close_date),
  reject_count = coalesce(target.reject_count, 0),
  scheduler_group_cd = coalesce(target.scheduler_group_cd, 'NVB_NIGHTLY')
WHEN NOT MATCHED THEN INSERT (batch_date, job_name, status, row_count, batch_id, src_system_cd,
  record_status_cd, effective_date, expiry_date, last_step_cd, window_start_date, window_end_date,
  reject_count, scheduler_group_cd)
VALUES (source.batch_date, source.job_name, 'COMPLETE', _close_count, _close_batch, 'COREBANK',
  'A', CAST(:window_start AS DATE), DATE '9999-12-31', '10_close_batch', CAST(:window_start AS DATE),
  _close_date, 0, 'NVB_NIGHTLY');
