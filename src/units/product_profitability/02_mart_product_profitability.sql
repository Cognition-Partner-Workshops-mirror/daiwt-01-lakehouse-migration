CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_product_profitability') AS
WITH product_income AS (
    SELECT
        a.product_id,
        -- Weight every retained account-day equally, including dormant accounts.
        AVG(CAST(b.balance AS DECIMAL(28,6))) AS precise_balance,
        MAX(CAST(p.annual_rate AS DECIMAL(6,2))) AS annual_rate
    FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.fct_daily_balance') b
    JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') a
        ON CAST(b.account_id AS INT) = a.account_id
    JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_product') p
        ON a.product_id = CAST(p.product_id AS INT)
    WHERE upper(rtrim(b.record_status_cd)) = 'A'
      AND upper(rtrim(a.record_status_cd)) = 'A'
      AND CAST(b.balance_date AS DATE) BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
    GROUP BY a.product_id
)
SELECT
    CAST(product_id AS INT) AS product_id,
    CAST(precise_balance AS DECIMAL(18,2)) AS average_balance,
    -- Calculate income from the unrounded mean and use decimal division.
    CAST(precise_balance * annual_rate / CAST(100 AS DECIMAL(3,0)) AS DECIMAL(18,2)) AS annual_income,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST('2025-06-30 23:00:00' AS TIMESTAMP) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST('2024-01-01' AS DATE) AS effective_date,
    CAST('9999-12-31' AS DATE) AS expiry_date
FROM product_income;
