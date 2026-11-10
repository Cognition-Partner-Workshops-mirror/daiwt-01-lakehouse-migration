CREATE OR REFRESH PRIVATE MATERIALIZED VIEW product_candidates
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT
  TRY_CAST(product_id AS INT) AS product_id,
  CAST(product_name AS STRING) AS product_name,
  CAST(CAST(annual_rate AS STRING) AS DECIMAL(6,2)) AS annual_rate,
  TRY_CAST(batch_id AS BIGINT) AS batch_id,
  TRY_CAST(load_ts AS TIMESTAMP_NTZ) AS load_ts,
  CAST(src_system_cd AS STRING) AS src_system_cd,
  CAST(record_status_cd AS STRING) AS record_status_cd,
  TRY_CAST(effective_date AS DATE) AS effective_date,
  TRY_CAST(expiry_date AS DATE) AS expiry_date,
  CAST(product_family_cd AS STRING) AS product_family_cd,
  CAST(currency_cd AS STRING) AS currency_cd,
  CAST(product_status_cd AS STRING) AS product_status_cd,
  CAST(rate_basis_cd AS STRING) AS rate_basis_cd,
  CAST(CAST(minimum_balance AS STRING) AS DECIMAL(18,2)) AS minimum_balance
FROM ${catalog}.${silver_schema}.asof_edw_dim_product(:drop)
UNION ALL
SELECT
  CAST(NULL AS INT), CAST('Synthetic bad product' AS STRING),
  CAST(1.00 AS DECIMAL(6,2)), CAST(20250630 AS BIGINT),
  CAST('2025-06-30 23:00:00' AS TIMESTAMP_NTZ),
  CAST('COREBANK' AS STRING), CAST('A' AS STRING),
  CAST('2024-01-01' AS DATE), CAST('9999-12-31' AS DATE),
  CAST('RETAIL' AS STRING), CAST('NOK' AS STRING),
  CAST('LIVE' AS STRING), CAST('ACT365' AS STRING),
  CAST(0.00 AS DECIMAL(18,2))
WHERE '${silver_bad_rows}' = 'true';

CREATE OR REFRESH MATERIALIZED VIEW dim_product (
  CONSTRAINT valid_product_id EXPECT (product_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_batch_id EXPECT (batch_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_load_ts EXPECT (load_ts IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_record_status EXPECT (record_status_cd IS NULL OR record_status_cd IN ('A', 'D')) ON VIOLATION DROP ROW,
  CONSTRAINT valid_minimum_balance EXPECT (minimum_balance IS NULL OR minimum_balance >= 0) ON VIOLATION DROP ROW
)
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT
  product_id, product_name, annual_rate, batch_id, load_ts,
  src_system_cd, record_status_cd, effective_date, expiry_date,
  product_family_cd, currency_cd, product_status_cd, rate_basis_cd, minimum_balance
FROM product_candidates;

CREATE OR REFRESH MATERIALIZED VIEW dim_product_quarantine
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT * FROM product_candidates
WHERE product_id IS NULL
   OR batch_id IS NULL
   OR load_ts IS NULL
   OR (record_status_cd IS NOT NULL AND record_status_cd NOT IN ('A', 'D'))
   OR minimum_balance < 0;
