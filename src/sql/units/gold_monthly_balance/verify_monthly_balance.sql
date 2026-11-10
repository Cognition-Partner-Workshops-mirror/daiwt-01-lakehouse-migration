CREATE OR REPLACE TEMPORARY VIEW golden_mart AS
SELECT account_id, month_start, average_balance, closing_balance, batch_id,
       src_system_cd, record_status_cd, effective_date, expiry_date
FROM read_files(:golden_path || '/outputs/' || :drop || '/monthly_balance/mart_monthly_balance.csv',
                format => 'csv', header => true, inferSchema => false, nullValue => '\\N');

CREATE OR REPLACE TEMPORARY VIEW actual_mart AS
SELECT CAST(account_id AS STRING) AS account_id, CAST(month_start AS STRING) AS month_start,
       CAST(average_balance AS STRING) AS average_balance, CAST(closing_balance AS STRING) AS closing_balance,
       CAST(batch_id AS STRING) AS batch_id, src_system_cd, record_status_cd,
       CAST(effective_date AS STRING) AS effective_date, CAST(expiry_date AS STRING) AS expiry_date
FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_monthly_balance');

SELECT CASE WHEN (SELECT count(*) FROM golden_mart) = (SELECT count(*) FROM actual_mart)
             AND (SELECT count(*) FROM (SELECT * FROM golden_mart EXCEPT ALL SELECT * FROM actual_mart)) = 0
             AND (SELECT count(*) FROM (SELECT * FROM actual_mart EXCEPT ALL SELECT * FROM golden_mart)) = 0
            THEN concat('PASS gold.mart_monthly_balance ', (SELECT count(*) FROM actual_mart), ' rows')
            ELSE raise_error('gold.mart_monthly_balance differs from golden') END AS parity;
