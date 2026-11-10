-- 05_customer_segment.btq + nvb_edw.customer_segment(as_of_date)
DECLARE OR REPLACE VARIABLE v_as_of_date DATE DEFAULT CAST(:as_of_date AS DATE);
DECLARE OR REPLACE VARIABLE v_batch_id BIGINT DEFAULT CAST(:batch_id AS BIGINT);
DECLARE OR REPLACE VARIABLE v_load_ts TIMESTAMP_NTZ DEFAULT CAST(:load_ts AS TIMESTAMP_NTZ);

SELECT CASE WHEN v_as_of_date IS NOT NULL AND v_batch_id IS NOT NULL AND v_load_ts IS NOT NULL
            THEN 'ok'
            ELSE raise_error('customer_segment: Segmentation requires a business date') END AS run_params;

USE CATALOG IDENTIFIER(:catalog);
USE SCHEMA IDENTIFIER(:silver_schema);

CREATE OR REPLACE TEMPORARY VIEW vt_customer_control AS
SELECT c.customer_id, count(a.account_id) AS accounts
FROM dim_customer AS c
LEFT JOIN dim_account AS a ON c.customer_id = a.customer_id
GROUP BY c.customer_id;

SELECT CASE WHEN count(*) = 0 THEN 'ok'
            ELSE raise_error('customer_segment: control failure, customer_id <= 0') END AS customer_ids
FROM vt_customer_control WHERE customer_id <= 0;

CREATE OR REPLACE TEMPORARY VIEW vt_customer_segment AS
SELECT
    c.customer_id,
    CAST(count(t.transaction_id) AS INT) AS txn_count,
    CAST(coalesce(sum(CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END), 0) AS DECIMAL(18,2)) AS spend,
    max(t.txn_date) AS last_txn_date,
    CASE
        WHEN max(t.txn_date) IS NULL OR max(t.txn_date) < date_sub(v_as_of_date, 90)
            THEN 'DORMANT'
        WHEN count(t.transaction_id) >= 50
         AND sum(CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END) >= 20000
            THEN 'PREMIUM'
        WHEN count(t.transaction_id) >= 20 THEN 'ACTIVE'
        ELSE 'OCCASIONAL'
    END AS segment
FROM dim_customer AS c
LEFT JOIN dim_account AS a
    ON c.customer_id = a.customer_id AND a.record_status_cd = 'A'
LEFT JOIN fct_transaction AS t
    ON a.account_id = t.account_id
   AND t.txn_date BETWEEN date_add(add_months(v_as_of_date, -12), 1) AND v_as_of_date
   AND t.record_status_cd = 'A'
WHERE c.record_status_cd = 'A'
GROUP BY c.customer_id;

SELECT CASE WHEN (SELECT count(*) FROM vt_customer_segment)
               = (SELECT count(*) FROM dim_customer WHERE record_status_cd = 'A')
            THEN 'ok'
            ELSE raise_error('customer_segment: Customer segment coverage failed') END AS coverage;

SELECT CASE WHEN count(*) = 0 THEN 'ok'
            ELSE raise_error('customer_segment: control failure, missing or invalid segment rows') END AS segment_controls
FROM vt_customer_control AS c
LEFT JOIN vt_customer_segment AS s ON c.customer_id = s.customer_id
WHERE s.customer_id IS NULL OR s.spend < 0
   OR s.segment NOT IN ('DORMANT', 'PREMIUM', 'ACTIVE', 'OCCASIONAL');

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_customer_segment') (
    customer_id INT,
    txn_count INT,
    spend DECIMAL(18,2),
    last_txn_date DATE,
    segment STRING,
    batch_id BIGINT NOT NULL,
    load_ts TIMESTAMP_NTZ NOT NULL,
    src_system_cd STRING,
    record_status_cd STRING,
    effective_date DATE,
    expiry_date DATE
)
TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported');

DELETE FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_customer_segment')
WHERE batch_id = v_batch_id;

INSERT INTO IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_customer_segment')
SELECT customer_id, txn_count, spend, last_txn_date, segment,
       v_batch_id, v_load_ts, 'COREBANK', 'A', DATE '2024-01-01', DATE '9999-12-31'
FROM vt_customer_segment;

SELECT segment, count(*) AS customers, sum(spend) AS debit_spend
FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_customer_segment')
GROUP BY segment ORDER BY segment;
