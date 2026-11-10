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

SELECT CASE WHEN count(*) = 1
  AND count_if(c.status = 'COMPLETE' AND c.batch_id = _nightly_batch AND c.reject_count = 0
    AND c.last_step_cd = '10_close_batch' AND c.row_count = tx.payment_count
    AND c.row_count = actual.payment_count AND tx.net_amount <=> actual.net_amount
    AND tx.first_date <=> actual.first_date AND tx.last_date <=> actual.last_date
    AND released.status = c.status AND released.row_count = c.row_count
    AND released.last_step_cd = c.last_step_cd
    AND tx.batch_id = c.batch_id AND released.batch_id = c.batch_id
    AND tx.batch_date = c.batch_date AND released.batch_date = c.batch_date
    AND tx.drop = :drop AND released.drop = :drop) = 1
  AND (SELECT count(*) FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.nightly_control_report')
       WHERE run_id = :run_id) = 2
  THEN 'NIGHTLY reconciled'
  ELSE raise_error('NIGHTLY reconciliation failed: control reports disagree with COMPLETE batch') END
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control') AS c
JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.nightly_control_report') AS tx
  ON tx.run_id = :run_id AND tx.report_name = '02_transactions'
JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.nightly_control_report') AS released
  ON released.run_id = :run_id AND released.report_name = 'nightly_release'
CROSS JOIN (SELECT count(*) AS payment_count, sum(amount) AS net_amount,
                   min(txn_date) AS first_date, max(txn_date) AS last_date
            FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction')) AS actual
WHERE c.batch_date = _nightly_date AND c.job_name = 'NIGHTLY';

UPDATE IDENTIFIER(:catalog || '.' || :silver_schema || '.nightly_run_state')
SET status = 'COMPLETE' WHERE run_id = :run_id;
