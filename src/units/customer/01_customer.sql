WITH customer_raw AS (
    SELECT
        TRY_CAST(TRIM(customer_id) AS INT) AS customer_id,
        TRIM(REPLACE(customer_name, CHAR(160), ' ')) AS customer_name,
        TRY_CAST(TRIM(branch_id) AS INT) AS branch_id,
        TRIM(REPLACE(town, CHAR(160), ' ')) AS town,
        DATE_TRUNC('SECOND', TRY_CAST(TRIM(updated_at) AS TIMESTAMP_NTZ)) AS updated_at
    FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.customer_feed')
)
SELECT CASE WHEN COUNT(*) = 0 THEN 0
            ELSE CAST(CONCAT('Invalid customer delivery: ', CAST(COUNT(*) AS STRING)) AS INT)
       END AS delivery_validation
FROM customer_raw AS r
LEFT JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_branch') AS b
    ON r.branch_id = b.branch_id
WHERE r.customer_id IS NULL OR r.customer_id <= 0
   OR r.customer_name IS NULL OR r.customer_name = ''
   OR r.updated_at IS NULL OR b.branch_id IS NULL;

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') AS
WITH customer_raw AS (
    SELECT
        TRY_CAST(TRIM(customer_id) AS INT) AS customer_id,
        TRIM(REPLACE(customer_name, CHAR(160), ' ')) AS customer_name,
        TRY_CAST(TRIM(branch_id) AS INT) AS branch_id,
        TRIM(REPLACE(town, CHAR(160), ' ')) AS town,
        DATE_TRUNC('SECOND', TRY_CAST(TRIM(updated_at) AS TIMESTAMP_NTZ)) AS updated_at
    FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.customer_feed')
)
SELECT
    CAST(customer_id AS INT) AS customer_id,
    CAST(customer_name AS STRING) AS customer_name,
    CAST(branch_id AS INT) AS branch_id,
    CAST(town AS STRING) AS town,
    CAST(updated_at AS TIMESTAMP_NTZ) AS updated_at,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST('2025-06-30 23:00:00' AS TIMESTAMP_NTZ) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST('2024-01-01' AS DATE) AS effective_date,
    CAST('9999-12-31' AS DATE) AS expiry_date,
    CAST('PERSONAL' AS STRING) AS customer_type_cd,
    CAST('NO' AS STRING) AS residence_country_cd,
    CAST('nb' AS STRING) AS preferred_language_cd,
    CAST('VERIFIED' AS STRING) AS kyc_status_cd,
    CAST('N' AS STRING) AS consent_marketing_ind
FROM customer_raw
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY customer_id
    ORDER BY updated_at DESC NULLS LAST,
             UPPER(customer_name) DESC NULLS LAST,
             UPPER(town) DESC NULLS LAST, branch_id DESC NULLS LAST
) = 1;
