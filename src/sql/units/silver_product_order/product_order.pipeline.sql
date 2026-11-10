CREATE OR REFRESH PRIVATE MATERIALIZED VIEW product_order_candidates
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT CAST(order_id AS INT) AS order_id,
          CAST(customer_id AS INT) AS customer_id,
          CAST(product_id AS INT) AS product_id,
          CAST(order_date AS DATE) AS order_date,
          CAST(status AS STRING) AS status,
          CAST(batch_id AS BIGINT) AS batch_id,
          CAST(load_ts AS TIMESTAMP_NTZ) AS load_ts,
          CAST(src_system_cd AS STRING) AS src_system_cd,
          CAST(record_status_cd AS STRING) AS record_status_cd,
          CAST(effective_date AS DATE) AS effective_date,
          CAST(expiry_date AS DATE) AS expiry_date,
          CAST(application_channel_cd AS STRING) AS application_channel_cd,
          CAST(decision_system_cd AS STRING) AS decision_system_cd,
          CAST(currency_cd AS STRING) AS currency_cd,
          CAST(manual_review_ind AS STRING) AS manual_review_ind
FROM ${catalog}.${silver_schema}.asof_edw_fct_product_order(:drop)
UNION ALL
SELECT CAST(NULL AS INT), CAST(1 AS INT), CAST(1 AS INT), DATE '2025-06-30',
       CAST('PENDING' AS STRING), CAST(20250630 AS BIGINT),
       CAST('2025-06-30 23:00:00' AS TIMESTAMP_NTZ), 'COREBANK', 'X',
       DATE '2024-01-01', DATE '9999-12-31', 'ONLINE', 'RETAIL_RULES', 'NOK', 'N'
WHERE '${silver_bad_rows}' = 'true';

CREATE OR REFRESH MATERIALIZED VIEW fct_product_order (
  CONSTRAINT valid_order_id EXPECT (order_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_record_status EXPECT (record_status_cd IN ('A','D')) ON VIOLATION DROP ROW,
  CONSTRAINT required_batch EXPECT (batch_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT required_load_ts EXPECT (load_ts IS NOT NULL) ON VIOLATION DROP ROW
)
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT order_id, customer_id, product_id, order_date, status, batch_id, load_ts,
          src_system_cd, record_status_cd, effective_date, expiry_date,
          application_channel_cd, decision_system_cd, currency_cd, manual_review_ind
FROM product_order_candidates;

CREATE OR REFRESH MATERIALIZED VIEW fct_product_order_quarantine
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT order_id, customer_id, product_id, order_date, status, batch_id, load_ts,
          src_system_cd, record_status_cd, effective_date, expiry_date,
          application_channel_cd, decision_system_cd, currency_cd, manual_review_ind
FROM product_order_candidates
WHERE NOT coalesce(order_id IS NOT NULL AND record_status_cd IN ('A','D')
                   AND batch_id IS NOT NULL AND load_ts IS NOT NULL, false);
