CREATE OR REFRESH PRIVATE MATERIALIZED VIEW calendar_candidates
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT
  CAST(calendar_date AS DATE) AS calendar_date,
  CAST(month_start AS DATE) AS month_start,
  CAST(year_no AS INT) AS year_no,
  CAST(month_no AS INT) AS month_no,
  CAST(batch_id AS BIGINT) AS batch_id,
  CAST(load_ts AS TIMESTAMP_NTZ) AS load_ts,
  CAST(src_system_cd AS STRING) AS src_system_cd,
  CAST(record_status_cd AS STRING) AS record_status_cd,
  CAST(effective_date AS DATE) AS effective_date,
  CAST(expiry_date AS DATE) AS expiry_date,
  CAST(calendar_name AS STRING) AS calendar_name,
  CAST(business_day_rule_cd AS STRING) AS business_day_rule_cd
FROM ${catalog}.${silver_schema}.asof_edw_dim_calendar(:drop)
UNION ALL
SELECT
  CAST(NULL AS DATE), CAST(NULL AS DATE), CAST(NULL AS INT), CAST(NULL AS INT),
  CAST(NULL AS BIGINT), CAST(NULL AS TIMESTAMP_NTZ), CAST(NULL AS STRING),
  CAST(NULL AS STRING), CAST(NULL AS DATE), CAST(NULL AS DATE),
  CAST(NULL AS STRING), CAST(NULL AS STRING)
WHERE '${silver_bad_rows}' = 'true';

CREATE OR REFRESH MATERIALIZED VIEW dim_calendar (
  CONSTRAINT valid_calendar_row EXPECT (
    calendar_date IS NOT NULL AND batch_id IS NOT NULL AND load_ts IS NOT NULL
    AND (record_status_cd IS NULL OR record_status_cd IN ('A', 'D'))
  ) ON VIOLATION DROP ROW
)
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT
  calendar_date, month_start, year_no, month_no, batch_id, load_ts,
  src_system_cd, record_status_cd, effective_date, expiry_date,
  calendar_name, business_day_rule_cd
FROM calendar_candidates;

CREATE OR REFRESH MATERIALIZED VIEW dim_calendar_quarantine
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT *, 'Invalid calendar key, required metadata or record status' AS reason
FROM calendar_candidates
WHERE NOT (
  calendar_date IS NOT NULL AND batch_id IS NOT NULL AND load_ts IS NOT NULL
  AND (record_status_cd IS NULL OR record_status_cd IN ('A', 'D'))
);
