CREATE OR REPLACE TEMPORARY VIEW month_control AS
SELECT b.account_id, trunc(b.balance_date, 'MM') AS month_start,
       count(*) AS ledger_days, max(b.balance_date) AS latest_date
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_daily_balance') AS b
GROUP BY 1, 2;

SELECT CASE WHEN count(*) = 0 THEN 'PASS month coverage'
            ELSE raise_error('monthly_balance rejected: incomplete daily ledger month') END AS control
FROM month_control
WHERE latest_date <> date_sub(add_months(month_start, 1), 1)
   OR ledger_days <> day(latest_date);

CREATE OR REPLACE TEMPORARY VIEW monthly_balance_rows AS
SELECT
  b.account_id,
  trunc(b.balance_date, 'MM') AS month_start,
  CAST(avg(CAST(b.balance AS DECIMAL(28,6))) AS DECIMAL(18,2)) AS average_balance,
  max(CASE WHEN b.balance_date = date_sub(add_months(trunc(b.balance_date, 'MM'), 1), 1)
           THEN b.balance END) AS closing_balance
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_daily_balance') AS b
INNER JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') AS a
  ON b.account_id = a.account_id
WHERE b.balance_date BETWEEN CAST(:window_start AS DATE) AND CAST(:as_of_date AS DATE)
  AND b.record_status_cd = 'A'
  AND a.record_status_cd = 'A'
GROUP BY 1, 2;

SELECT CASE WHEN count(*) = 0 THEN 'PASS month publication'
            ELSE raise_error('monthly_balance rejected: account-month missing or without closing balance') END AS control
FROM month_control AS w
LEFT JOIN monthly_balance_rows AS m
  ON w.account_id = m.account_id AND w.month_start = m.month_start
WHERE m.account_id IS NULL OR m.closing_balance IS NULL;

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_monthly_balance') (
  account_id INT,
  month_start DATE,
  average_balance DECIMAL(18,2),
  closing_balance DECIMAL(18,2),
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE
) AS
SELECT
  account_id,
  month_start,
  average_balance,
  CAST(closing_balance AS DECIMAL(18,2)) AS closing_balance,
  CAST(:batch_id AS BIGINT) AS batch_id,
  CAST(:load_ts AS TIMESTAMP) AS load_ts,
  'COREBANK' AS src_system_cd,
  'A' AS record_status_cd,
  DATE '2024-01-01' AS effective_date,
  DATE '9999-12-31' AS expiry_date
FROM monthly_balance_rows;
