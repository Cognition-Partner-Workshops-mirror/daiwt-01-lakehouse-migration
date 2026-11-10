SET ANSI_MODE = true;
DECLARE OR REPLACE VARIABLE _check_date DATE;
SET VAR _check_date = (
  SELECT max(_business_date)
  FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.stg_transaction_feed')
  WHERE _drop = IDENTIFIER(:catalog || '.' || :silver_schema || '.resolve_drop')(:drop)
);
SELECT CASE WHEN count(*) = 1
             AND count_if(c.status = 'COMPLETE' AND c.last_step_cd = '10_close_batch'
                          AND c.row_count = t.txn_rows AND c.window_end_date = _check_date
                          AND c.window_start_date IS NOT NULL AND c.batch_id IS NOT NULL) = 1
            THEN 'PASS close_batch: NIGHTLY batch COMPLETE'
            ELSE raise_error('close_batch verification failed') END AS verification
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control') AS c
CROSS JOIN (SELECT count(*) AS txn_rows
            FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction')) AS t
WHERE c.batch_date = _check_date AND c.job_name = 'NIGHTLY';
