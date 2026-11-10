CREATE OR REFRESH PRIVATE MATERIALIZED VIEW card_typed
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT
  CAST(card_id AS INT) AS card_id,
  CAST(account_id AS INT) AS account_id,
  CAST(card_type AS STRING) AS card_type,
  CAST(batch_id AS BIGINT) AS batch_id,
  CAST(load_ts AS TIMESTAMP_NTZ) AS load_ts,
  CAST(src_system_cd AS STRING) AS src_system_cd,
  CAST(record_status_cd AS STRING) AS record_status_cd,
  CAST(effective_date AS DATE) AS effective_date,
  CAST(expiry_date AS DATE) AS expiry_date,
  CAST(network_cd AS STRING) AS network_cd,
  CAST(card_status_cd AS STRING) AS card_status_cd,
  CAST(card_expiry_date AS DATE) AS card_expiry_date,
  CAST(contactless_ind AS STRING) AS contactless_ind,
  CAST(online_enabled_ind AS STRING) AS online_enabled_ind
FROM ${catalog}.${silver_schema}.asof_edw_dim_card(:drop);

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW card_candidates
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT * FROM card_typed
UNION ALL
SELECT
  CAST(NULL AS INT) AS card_id,
  CAST(1 AS INT) AS account_id,
  CAST('DEBIT' AS STRING) AS card_type,
  CAST(20250630 AS BIGINT) AS batch_id,
  CAST('2025-06-30 23:00:00' AS TIMESTAMP_NTZ) AS load_ts,
  CAST('COREBANK' AS STRING) AS src_system_cd,
  CAST('A' AS STRING) AS record_status_cd,
  CAST('2024-01-01' AS DATE) AS effective_date,
  CAST('9999-12-31' AS DATE) AS expiry_date,
  CAST('BANKAXEPT' AS STRING) AS network_cd,
  CAST('ACTIVE' AS STRING) AS card_status_cd,
  CAST('2028-01-31' AS DATE) AS card_expiry_date,
  CAST('Y' AS STRING) AS contactless_ind,
  CAST('Y' AS STRING) AS online_enabled_ind
WHERE '${silver_bad_rows}' = 'true';

CREATE OR REFRESH MATERIALIZED VIEW dim_card (
  CONSTRAINT valid_card_id EXPECT (card_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_batch_id EXPECT (batch_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_load_ts EXPECT (load_ts IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_record_status EXPECT (record_status_cd IS NULL OR record_status_cd IN ('A','D')) ON VIOLATION DROP ROW
)
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT card_id, account_id, card_type, batch_id, load_ts, src_system_cd,
  record_status_cd, effective_date, expiry_date, network_cd, card_status_cd,
  card_expiry_date, contactless_ind, online_enabled_ind
FROM card_candidates;

CREATE OR REFRESH MATERIALIZED VIEW dim_card_quarantine
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT *, 'invalid card key, required audit field or record status' AS reason
FROM card_candidates
WHERE card_id IS NULL OR batch_id IS NULL OR load_ts IS NULL
  OR (record_status_cd IS NOT NULL AND record_status_cd NOT IN ('A','D'));
