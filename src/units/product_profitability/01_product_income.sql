CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.product_income')
USING DELTA AS
WITH parameters AS (
    SELECT DATE '2024-01-01' AS window_start, DATE '2025-06-30' AS as_of_date
)
SELECT
    a.product_id,
    AVG(CAST(b.balance AS DECIMAL(28,6))) AS precise_balance,
    MAX(CAST(p.annual_rate AS DECIMAL(9,4))) AS annual_rate
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.fct_daily_balance') AS b
INNER JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') AS a
  ON b.account_id = a.account_id
INNER JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_product') AS p
  ON a.product_id = p.product_id
CROSS JOIN parameters AS params
WHERE upper(rtrim(b.record_status_cd)) = 'A'
  AND upper(rtrim(a.record_status_cd)) = 'A'
  AND CAST(b.balance_date AS DATE) BETWEEN params.window_start AND params.as_of_date
GROUP BY a.product_id;
