CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_product_profitability')
USING DELTA AS
SELECT
    CAST(product_id AS INT) AS product_id,
    CAST(precise_balance AS DECIMAL(18,2)) AS average_balance,
    CAST(precise_balance * annual_rate / CAST(100 AS DECIMAL(3,0)) AS DECIMAL(18,2)) AS annual_income,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST(TIMESTAMP '2025-06-30 23:00:00+00:00' AS TIMESTAMP) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST(DATE '2024-01-01' AS DATE) AS effective_date,
    CAST(DATE '9999-12-31' AS DATE) AS expiry_date
FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.product_income');
