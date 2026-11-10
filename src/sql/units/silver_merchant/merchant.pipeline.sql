CREATE OR REFRESH PRIVATE MATERIALIZED VIEW merchant_candidates
TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')
AS SELECT
  CAST(merchant_id AS INT) AS merchant_id,
  CAST(merchant_name AS STRING) AS merchant_name,
  CAST(category AS STRING) AS category,
  CAST(batch_id AS BIGINT) AS batch_id,
  CAST(load_ts AS TIMESTAMP_NTZ) AS load_ts,
  CAST(src_system_cd AS STRING) AS src_system_cd,
  CAST(record_status_cd AS STRING) AS record_status_cd,
  CAST(effective_date AS DATE) AS effective_date,
  CAST(expiry_date AS DATE) AS expiry_date,
  CAST(country_cd AS STRING) AS country_cd,
  CAST(settlement_currency_cd AS STRING) AS settlement_currency_cd,
  CAST(acquirer_cd AS STRING) AS acquirer_cd,
  CAST(merchant_status_cd AS STRING) AS merchant_status_cd
FROM ${catalog}.${silver_schema}.asof_edw_dim_merchant(:drop)
UNION ALL
SELECT
  CAST(NULL AS INT), CAST('Synthetic null-key merchant' AS STRING), CAST(NULL AS STRING),
  CAST(20250630 AS BIGINT), CAST('2025-06-30 23:00:00' AS TIMESTAMP_NTZ),
  CAST('COREBANK' AS STRING), CAST('A' AS STRING), CAST('2024-01-01' AS DATE),
  CAST('9999-12-31' AS DATE), CAST('NO' AS STRING), CAST('NOK' AS STRING),
  CAST('NVB' AS STRING), CAST('ACTIVE' AS STRING)
WHERE '${silver_bad_rows}' = 'true';

CREATE OR REFRESH MATERIALIZED VIEW ${catalog}.${silver_schema}.dim_merchant (
  CONSTRAINT valid_merchant_id EXPECT (merchant_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_batch_id EXPECT (batch_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_load_ts EXPECT (load_ts IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_record_status EXPECT (record_status_cd IN ('A', 'D')) ON VIOLATION DROP ROW
)
TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')
AS SELECT merchant_id, merchant_name, category, batch_id, load_ts, src_system_cd,
  record_status_cd, effective_date, expiry_date, country_cd, settlement_currency_cd,
  acquirer_cd, merchant_status_cd
FROM merchant_candidates;

CREATE OR REFRESH MATERIALIZED VIEW ${catalog}.${silver_schema}.dim_merchant_quarantine
TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')
AS SELECT merchant_id, merchant_name, category, batch_id, load_ts, src_system_cd,
  record_status_cd, effective_date, expiry_date, country_cd, settlement_currency_cd,
  acquirer_cd, merchant_status_cd, 'invalid merchant key, batch, timestamp or record status' AS reason_rejected
FROM merchant_candidates
WHERE merchant_id IS NULL OR batch_id IS NULL OR load_ts IS NULL
  OR record_status_cd IS NULL OR record_status_cd NOT IN ('A', 'D');
