CREATE OR REFRESH PRIVATE MATERIALIZED VIEW customer_parameters AS
SELECT CAST(20250630 AS BIGINT) AS batch_id,
       DATE '2024-01-01' AS window_start,
       DATE '2025-06-30' AS as_of_date;

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW customer_candidates AS
SELECT try_cast(trim(customer_id) AS INT) AS customer_id,
       trim(replace(customer_name, char(160), ' ')) AS customer_name,
       try_cast(trim(branch_id) AS INT) AS branch_id,
       trim(replace(town, char(160), ' ')) AS town,
       CAST(try_to_timestamp(trim(updated_at), 'yyyy-MM-dd HH:mm:ss') AS TIMESTAMP) AS updated_at,
       false AS _demo_row
FROM ${catalog}.${silver_schema}.asof_stg_customer_feed(:drop);

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW customer_checked AS
SELECT r.*,
       (r.customer_id IS NOT NULL AND r.customer_id > 0
        AND r.customer_name IS NOT NULL AND r.customer_name <> ''
        AND r.updated_at IS NOT NULL AND b.branch_id IS NOT NULL) AS valid_customer
FROM customer_candidates r
LEFT JOIN ${catalog}.${silver_schema}.dim_branch b ON r.branch_id = b.branch_id;

CREATE OR REFRESH MATERIALIZED VIEW ${catalog}.${silver_schema}.dim_customer_quarantine AS
SELECT customer_id, customer_name, branch_id, town, updated_at,
       'Invalid customer ID, name, timestamp or branch reference' AS reason
FROM customer_checked
WHERE NOT valid_customer;

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW customer_delivery AS
SELECT customer_id, customer_name, branch_id, town, updated_at
FROM customer_checked
WHERE NOT _demo_row
  AND CASE WHEN (SELECT count(*) FROM customer_checked WHERE NOT valid_customer AND NOT _demo_row) = 0 THEN true
           ELSE raise_error('Customer delivery rejected: invalid customer rows') END
QUALIFY row_number() OVER (
    PARTITION BY customer_id
    ORDER BY updated_at DESC, customer_name DESC, town DESC, branch_id DESC
) = 1;

CREATE OR REFRESH MATERIALIZED VIEW ${catalog}.${silver_schema}.dim_customer (
    customer_id INT,
    customer_name STRING,
    branch_id INT,
    town STRING,
    updated_at TIMESTAMP,
    batch_id BIGINT,
    load_ts TIMESTAMP,
    src_system_cd STRING,
    record_status_cd STRING,
    effective_date DATE,
    expiry_date DATE,
    customer_type_cd STRING,
    residence_country_cd STRING,
    preferred_language_cd STRING,
    kyc_status_cd STRING,
    consent_marketing_ind STRING,
    CONSTRAINT valid_customer_id EXPECT (customer_id IS NOT NULL AND customer_id > 0) ON VIOLATION FAIL UPDATE,
    CONSTRAINT valid_customer_name EXPECT (customer_name IS NOT NULL AND customer_name <> '') ON VIOLATION FAIL UPDATE,
    CONSTRAINT valid_customer_timestamp EXPECT (updated_at IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT valid_customer_branch EXPECT (branch_id IS NOT NULL) ON VIOLATION FAIL UPDATE
)
AS SELECT c.customer_id, c.customer_name, c.branch_id, c.town, c.updated_at,
          p.batch_id,
          CAST(concat(CAST(p.as_of_date AS STRING), ' 23:00:00') AS TIMESTAMP) AS load_ts,
          'COREBANK' AS src_system_cd, 'A' AS record_status_cd,
          p.window_start AS effective_date, DATE '9999-12-31' AS expiry_date,
          'PERSONAL' AS customer_type_cd, 'NO' AS residence_country_cd,
          'nb' AS preferred_language_cd, 'VERIFIED' AS kyc_status_cd,
          'N' AS consent_marketing_ind
FROM customer_delivery c CROSS JOIN customer_parameters p;
