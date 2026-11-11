WITH product_income AS (
    SELECT
        a.product_id,
        AVG(CAST(b.balance AS DECIMAL(28,6))) AS precise_balance,
        MAX(CAST(p.annual_rate AS DECIMAL(6,2))) AS annual_rate
    FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.fct_daily_balance') b
    JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') a
        ON CAST(b.account_id AS INT) = a.account_id
    JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_product') p
        ON a.product_id = CAST(p.product_id AS INT)
    -- Match Teradata character comparisons without altering delivered values.
    WHERE upper(rtrim(b.record_status_cd)) = 'A'
      AND upper(rtrim(a.record_status_cd)) = 'A'
      AND CAST(b.balance_date AS DATE) BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
    GROUP BY a.product_id
)
SELECT CASE WHEN EXISTS (
    SELECT 1 FROM product_income
    WHERE annual_rate IS NULL OR annual_rate < 0 OR precise_balance IS NULL
) THEN CAST('Invalid product profitability rate or mean balance' AS INT) ELSE 1 END;
