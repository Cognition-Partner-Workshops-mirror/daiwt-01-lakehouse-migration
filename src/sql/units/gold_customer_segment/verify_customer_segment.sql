-- Exact multiset comparison of gold.mart_customer_segment with the drop's golden CSV.
CREATE OR REPLACE TEMPORARY VIEW vt_golden AS
SELECT * FROM read_files(
    :golden_path || '/outputs/' || :drop || '/customer_segment/mart_customer_segment.csv',
    format => 'csv', header => true, nullValue => '\\N', mode => 'FAILFAST',
    schema => 'customer_id STRING, txn_count STRING, spend STRING, last_txn_date STRING, segment STRING, batch_id STRING, src_system_cd STRING, record_status_cd STRING, effective_date STRING, expiry_date STRING');

CREATE OR REPLACE TEMPORARY VIEW vt_actual AS
SELECT CAST(customer_id AS STRING) AS customer_id, CAST(txn_count AS STRING) AS txn_count,
       CAST(spend AS STRING) AS spend, CAST(last_txn_date AS STRING) AS last_txn_date,
       segment, CAST(batch_id AS STRING) AS batch_id, src_system_cd, record_status_cd,
       CAST(effective_date AS STRING) AS effective_date, CAST(expiry_date AS STRING) AS expiry_date
FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_customer_segment');

SELECT CASE WHEN golden_rows > 0 AND golden_rows = actual_rows AND missing = 0 AND unexpected = 0
            THEN concat('PASS gold.mart_customer_segment ', actual_rows, ' rows')
            ELSE raise_error(concat('FAIL gold.mart_customer_segment golden=', golden_rows,
                 ' actual=', actual_rows, ' missing=', missing, ' unexpected=', unexpected)) END AS parity
FROM (
    SELECT (SELECT count(*) FROM vt_golden) AS golden_rows,
           (SELECT count(*) FROM vt_actual) AS actual_rows,
           (SELECT count(*) FROM (SELECT * FROM vt_golden EXCEPT ALL SELECT * FROM vt_actual)) AS missing,
           (SELECT count(*) FROM (SELECT * FROM vt_actual EXCEPT ALL SELECT * FROM vt_golden)) AS unexpected
);
