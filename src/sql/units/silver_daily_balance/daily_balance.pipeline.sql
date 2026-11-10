CREATE OR REFRESH PRIVATE MATERIALIZED VIEW daily_balance_candidates
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT
  CAST(account_id AS INT) AS account_id,
  CAST(balance_date AS DATE) AS balance_date,
  CAST(balance AS DECIMAL(18,2)) AS balance,
  CAST(batch_id AS BIGINT) AS batch_id,
  CAST(load_ts AS TIMESTAMP_NTZ) AS load_ts,
  CAST(src_system_cd AS STRING) AS src_system_cd,
  CAST(record_status_cd AS STRING) AS record_status_cd,
  CAST(effective_date AS DATE) AS effective_date,
  CAST(expiry_date AS DATE) AS expiry_date,
  CAST(balance_type_cd AS STRING) AS balance_type_cd,
  CAST(currency_cd AS STRING) AS currency_cd,
  CAST(accrued_interest AS DECIMAL(18,2)) AS accrued_interest,
  CAST(reserved_amount AS DECIMAL(18,2)) AS reserved_amount
FROM ${catalog}.${silver_schema}.asof_edw_fct_daily_balance(:drop)
UNION ALL
SELECT CAST(-1 AS INT), DATE '2024-01-01', CAST(0 AS DECIMAL(18,2)),
       CAST(20250630 AS BIGINT), CAST('2025-06-30 23:00:00' AS TIMESTAMP_NTZ),
       CAST('COREBANK' AS STRING), CAST('X' AS STRING),
       DATE '2024-01-01', DATE '9999-12-31', CAST('BOOK' AS STRING),
       CAST('NOK' AS STRING), CAST(0.00 AS DECIMAL(18,2)), CAST(0.00 AS DECIMAL(18,2))
WHERE '${silver_bad_rows}' = 'true';

CREATE OR REFRESH MATERIALIZED VIEW fct_daily_balance (
  CONSTRAINT required_batch EXPECT (batch_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT required_load_ts EXPECT (load_ts IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_record_status EXPECT (record_status_cd IS NULL OR record_status_cd IN ('A','D')) ON VIOLATION DROP ROW
)
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT * FROM daily_balance_candidates;

CREATE OR REFRESH MATERIALIZED VIEW daily_balance_quarantine
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT *, 'DDL constraint violation' AS reason
FROM daily_balance_candidates
WHERE batch_id IS NULL OR load_ts IS NULL
   OR (record_status_cd IS NOT NULL AND record_status_cd NOT IN ('A','D'));
