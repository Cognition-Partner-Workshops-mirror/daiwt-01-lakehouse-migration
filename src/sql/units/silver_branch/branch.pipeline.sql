CREATE OR REFRESH PRIVATE MATERIALIZED VIEW branch_candidates
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT
  CAST(branch_id AS INT) AS branch_id,
  CAST(town AS STRING) AS town,
  CAST(batch_id AS BIGINT) AS batch_id,
  CAST(load_ts AS TIMESTAMP_NTZ) AS load_ts,
  CAST(src_system_cd AS STRING) AS src_system_cd,
  CAST(record_status_cd AS STRING) AS record_status_cd,
  CAST(effective_date AS DATE) AS effective_date,
  CAST(expiry_date AS DATE) AS expiry_date,
  CAST(branch_name AS STRING) AS branch_name,
  CAST(region_cd AS STRING) AS region_cd,
  CAST(branch_type_cd AS STRING) AS branch_type_cd,
  CAST(service_language_cd AS STRING) AS service_language_cd,
  CAST(branch_status_cd AS STRING) AS branch_status_cd
FROM ${catalog}.${silver_schema}.asof_edw_dim_branch(:drop)
UNION ALL
SELECT CAST(NULL AS INT), CAST('Synthetic branch' AS STRING), CAST(NULL AS BIGINT), CAST(NULL AS TIMESTAMP_NTZ), CAST(NULL AS STRING), CAST('X' AS STRING), CAST(NULL AS DATE), CAST(NULL AS DATE), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING)
WHERE '${silver_bad_rows}' = 'true';

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW branch_checked
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT *,
  nullif(concat_ws('; ',
    CASE WHEN branch_id IS NULL THEN 'null branch_id' END,
    CASE WHEN branch_key_count <> 1 THEN 'duplicate branch_id' END,
    CASE WHEN batch_id IS NULL THEN 'null batch_id' END,
    CASE WHEN load_ts IS NULL THEN 'null load_ts' END,
    CASE WHEN record_status_cd IS NULL OR record_status_cd NOT IN ('A','D') THEN 'invalid record_status_cd' END,
    CASE WHEN length(town) > 30 THEN 'town exceeds 30' END,
    CASE WHEN length(src_system_cd) > 12 THEN 'src_system_cd exceeds 12' END,
    CASE WHEN length(branch_name) > 80 THEN 'branch_name exceeds 80' END,
    CASE WHEN length(region_cd) > 8 THEN 'region_cd exceeds 8' END,
    CASE WHEN length(branch_type_cd) > 10 THEN 'branch_type_cd exceeds 10' END,
    CASE WHEN length(service_language_cd) > 2 THEN 'service_language_cd exceeds 2' END,
    CASE WHEN length(branch_status_cd) > 10 THEN 'branch_status_cd exceeds 10' END
  ), '') AS reason
FROM (
  SELECT *, count(*) OVER (PARTITION BY branch_id) AS branch_key_count
  FROM branch_candidates
);

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW branch_validated (
  CONSTRAINT valid_branch EXPECT (reason IS NULL) ON VIOLATION DROP ROW,
  CONSTRAINT unique_branch_id EXPECT (branch_key_count = 1) ON VIOLATION DROP ROW
)
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT * FROM branch_checked;

CREATE OR REFRESH MATERIALIZED VIEW ${catalog}.${silver_schema}.dim_branch (
  branch_id INT,
  town STRING,
  batch_id BIGINT,
  load_ts TIMESTAMP_NTZ,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  branch_name STRING,
  region_cd STRING,
  branch_type_cd STRING,
  service_language_cd STRING,
  branch_status_cd STRING,
  CONSTRAINT valid_branch_id EXPECT (branch_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_batch_id EXPECT (batch_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_load_ts EXPECT (load_ts IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_record_status EXPECT (record_status_cd IN ('A','D')) ON VIOLATION DROP ROW
)
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT branch_id, town, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, branch_name, region_cd, branch_type_cd, service_language_cd, branch_status_cd
FROM branch_validated;

CREATE OR REFRESH MATERIALIZED VIEW ${catalog}.${silver_schema}.dim_branch_quarantine
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT branch_id, town, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, branch_name, region_cd, branch_type_cd, service_language_cd, branch_status_cd, reason
FROM branch_checked
WHERE reason IS NOT NULL;
