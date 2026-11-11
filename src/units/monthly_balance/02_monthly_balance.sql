CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_monthly_balance') AS
SELECT
    CAST(b.account_id AS INT) AS account_id,
    CAST(trunc(CAST(b.balance_date AS DATE), 'MONTH') AS DATE) AS month_start,
    -- Two-decimal balances and at most 31 days make six decimals sufficient
    -- to retain every cent-rounding boundary before the final HALF_UP cast.
    CAST(CAST(AVG(CAST(b.balance AS DECIMAL(28,6))) AS DECIMAL(32,6)) AS DECIMAL(18,2)) AS average_balance,
    CAST(MAX(CASE
        WHEN CAST(b.balance_date AS DATE) = last_day(CAST(b.balance_date AS DATE))
        THEN CAST(b.balance AS DECIMAL(18,2))
    END) AS DECIMAL(18,2)) AS closing_balance,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST('2025-06-30T23:00:00.000Z' AS TIMESTAMP) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST('2024-01-01' AS DATE) AS effective_date,
    CAST('9999-12-31' AS DATE) AS expiry_date
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.fct_daily_balance') b
JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') a
    ON CAST(b.account_id AS INT) = a.account_id
WHERE CAST(b.balance_date AS DATE) BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
  -- Teradata character equality ignores case and trailing CHAR padding.
  AND upper(rtrim(b.record_status_cd)) = 'A'
  AND upper(rtrim(a.record_status_cd)) = 'A'
GROUP BY b.account_id, CAST(trunc(CAST(b.balance_date AS DATE), 'MONTH') AS DATE);
