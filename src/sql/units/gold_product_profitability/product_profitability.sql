CREATE OR REPLACE TEMPORARY VIEW vt_product_income AS
SELECT a.product_id,
       avg(CAST(b.balance AS DECIMAL(28,6))) AS precise_balance,
       max(p.annual_rate) AS annual_rate
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_daily_balance') AS b
JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') AS a ON b.account_id = a.account_id
JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_product') AS p ON a.product_id = p.product_id
WHERE b.record_status_cd = 'A' AND a.record_status_cd = 'A'
  AND b.balance_date BETWEEN CAST(:window_start AS DATE) AND CAST(:as_of_date AS DATE)
GROUP BY a.product_id;

SELECT CASE WHEN count(*) = 0 THEN 'PASS product income controls'
            ELSE raise_error('Control failure: product with NULL/negative annual_rate or NULL balance; delivery not published') END AS control_check
FROM vt_product_income
WHERE annual_rate IS NULL OR annual_rate < 0 OR precise_balance IS NULL;

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_product_profitability')
TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')
AS SELECT
  CAST(product_id AS INT) AS product_id,
  CAST(precise_balance AS DECIMAL(18,2)) AS average_balance,
  CAST(precise_balance * annual_rate / 100 AS DECIMAL(18,2)) AS annual_income,
  CAST(date_format(CAST(:as_of_date AS DATE), 'yyyyMMdd') AS BIGINT) AS batch_id,
  CAST(:load_ts AS TIMESTAMP_NTZ) AS load_ts,
  'COREBANK' AS src_system_cd,
  'A' AS record_status_cd,
  CAST(:window_start AS DATE) AS effective_date,
  DATE '9999-12-31' AS expiry_date
FROM vt_product_income;
