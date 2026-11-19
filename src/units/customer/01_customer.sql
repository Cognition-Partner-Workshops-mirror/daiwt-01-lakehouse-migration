CREATE OR REPLACE TEMPORARY VIEW customer_raw AS
SELECT
    TRY_CAST(TRIM(customer_id) AS INT) AS customer_id,
    TRIM(REPLACE(customer_name, CHR(160), ' ')) AS customer_name,
    TRY_CAST(TRIM(branch_id) AS INT) AS branch_id,
    TRIM(REPLACE(town, CHR(160), ' ')) AS town,
    DATE_TRUNC('SECOND', TRY_CAST(TRIM(updated_at) AS TIMESTAMP)) AS updated_at
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.customer_feed');

SELECT CASE WHEN COUNT(*) = 0 THEN 1
    ELSE raise_error('Invalid customer delivery: identifier, name, timestamp or branch') END
FROM customer_raw AS r
LEFT JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_branch') AS b
    ON r.branch_id = b.branch_id
WHERE r.customer_id IS NULL OR r.customer_id <= 0
    OR r.customer_name IS NULL OR r.customer_name = ''
    OR r.updated_at IS NULL OR b.branch_id IS NULL;

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :silver_schema || '.customer_delivery') AS
SELECT
    CAST(customer_id AS INT) AS customer_id,
    CAST(customer_name AS STRING) AS customer_name,
    CAST(branch_id AS INT) AS branch_id,
    CAST(town AS STRING) AS town,
    CAST(updated_at AS TIMESTAMP) AS updated_at,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST(TIMESTAMP '2025-06-30 23:00:00' AS TIMESTAMP) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST(DATE '2024-01-01' AS DATE) AS effective_date,
    CAST(DATE '9999-12-31' AS DATE) AS expiry_date,
    CAST('PERSONAL' AS STRING) AS customer_type_cd,
    CAST('NO' AS STRING) AS residence_country_cd,
    CAST('nb' AS STRING) AS preferred_language_cd,
    CAST('VERIFIED' AS STRING) AS kyc_status_cd,
    CAST('N' AS STRING) AS consent_marketing_ind
FROM customer_raw
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY customer_id
    ORDER BY updated_at DESC, UPPER(customer_name) DESC NULLS LAST,
        UPPER(town) DESC NULLS LAST, branch_id DESC
) = 1;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') AS
SELECT * FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.customer_delivery')
WHERE FALSE;

MERGE INTO IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') AS t
USING IDENTIFIER(:catalog || '.' || :silver_schema || '.customer_delivery') AS s
ON t.customer_id = s.customer_id
WHEN MATCHED THEN UPDATE SET
    customer_name = s.customer_name,
    branch_id = s.branch_id,
    town = s.town,
    updated_at = s.updated_at,
    batch_id = CAST(20250630 AS BIGINT),
    load_ts = TIMESTAMP '2025-06-30 23:00:00'
WHEN NOT MATCHED THEN INSERT (
    customer_id, customer_name, branch_id, town, updated_at, batch_id, load_ts,
    src_system_cd, record_status_cd, effective_date, expiry_date,
    customer_type_cd, residence_country_cd, preferred_language_cd,
    kyc_status_cd, consent_marketing_ind
) VALUES (
    s.customer_id, s.customer_name, s.branch_id, s.town, s.updated_at,
    CAST(20250630 AS BIGINT), TIMESTAMP '2025-06-30 23:00:00',
    CAST('COREBANK' AS STRING), CAST('A' AS STRING), DATE '2024-01-01', DATE '9999-12-31',
    CAST('PERSONAL' AS STRING), CAST('NO' AS STRING), CAST('nb' AS STRING),
    CAST('VERIFIED' AS STRING), CAST('N' AS STRING)
);
