CREATE OR REPLACE TEMPORARY VIEW customer_segment_parameters AS
SELECT
    DATE '2025-06-30' AS as_of_date,
    DATE '2024-01-01' AS window_start,
    CAST(20250630 AS BIGINT) AS batch_id;

SELECT CASE WHEN as_of_date IS NOT NULL THEN 1
    ELSE CAST('Segmentation requires a business date' AS INT) END
FROM customer_segment_parameters;

SELECT CASE WHEN COUNT(*) = 0 THEN 1
    ELSE CAST('Invalid customer identifier for segmentation' AS INT) END
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
WHERE customer_id IS NULL OR customer_id <= 0;

CREATE OR REPLACE TEMPORARY VIEW customer_segment_delivery AS
WITH activity AS (
    SELECT
        c.customer_id,
        COUNT(t.transaction_id) AS txn_count,
        COALESCE(SUM(CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END), 0) AS spend,
        MAX(t.txn_date) AS last_txn_date
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') AS c
    CROSS JOIN customer_segment_parameters AS p
    LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') AS a
        ON c.customer_id = a.customer_id
        AND UPPER(RTRIM(a.record_status_cd)) = 'A'
    LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
        ON a.account_id = t.account_id
        AND t.txn_date BETWEEN date_add(add_months(p.as_of_date, -12), 1) AND p.as_of_date
        AND UPPER(RTRIM(t.record_status_cd)) = 'A'
    WHERE UPPER(RTRIM(c.record_status_cd)) = 'A'
    GROUP BY c.customer_id
)
SELECT
    CAST(a.customer_id AS INT) AS customer_id,
    CAST(a.txn_count AS INT) AS txn_count,
    CAST(a.spend AS DECIMAL(18, 2)) AS spend,
    CAST(a.last_txn_date AS DATE) AS last_txn_date,
    CAST(CASE
        WHEN a.last_txn_date IS NULL OR a.last_txn_date < date_sub(p.as_of_date, 90) THEN 'DORMANT'
        WHEN a.txn_count >= 50 AND a.spend >= 20000 THEN 'PREMIUM'
        WHEN a.txn_count >= 20 THEN 'ACTIVE'
        ELSE 'OCCASIONAL'
    END AS STRING) AS segment,
    CAST(p.batch_id AS BIGINT) AS batch_id,
    TIMESTAMP '2025-06-30 23:00:00Z' AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST(p.window_start AS DATE) AS effective_date,
    CAST(DATE '9999-12-31' AS DATE) AS expiry_date
FROM activity AS a
CROSS JOIN customer_segment_parameters AS p;

SELECT CASE WHEN
    (SELECT COUNT(*) FROM customer_segment_delivery) =
    (SELECT COUNT(*) FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
     WHERE UPPER(RTRIM(record_status_cd)) = 'A')
    THEN 1 ELSE CAST('Customer segment coverage failed' AS INT) END;

SELECT CASE WHEN COUNT(*) = 0 THEN 1
    ELSE CAST('Invalid customer segment or spend' AS INT) END
FROM customer_segment_delivery
WHERE spend IS NULL OR spend < 0
    OR segment IS NULL OR segment NOT IN ('DORMANT', 'PREMIUM', 'ACTIVE', 'OCCASIONAL');

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_customer_segment')
USING DELTA AS
SELECT
    customer_id, txn_count, spend, last_txn_date, segment, batch_id, load_ts,
    src_system_cd, record_status_cd, effective_date, expiry_date
FROM customer_segment_delivery;
