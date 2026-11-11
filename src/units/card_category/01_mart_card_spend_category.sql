-- 09_card_category: card debit spend by merchant category for the batch window.
-- Any control failure aborts the CTAS (ANSI cast error), so nothing is written.
CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_card_spend_category') AS
WITH params AS (
  SELECT
    DATE '2024-01-01' AS window_start,
    DATE '2025-06-30' AS as_of_date,
    CAST(20250630 AS BIGINT) AS batch_id,
    TIMESTAMP '2025-06-30 23:00:00' AS load_ts
),
merchant AS (
  SELECT
    TRY_CAST(TRIM(CAST(merchant_id AS STRING)) AS INT) AS merchant_id,
    category,
    record_status_cd
  FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_merchant')
),
card_txn AS (
  SELECT t.transaction_id, t.amount, m.category
  FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
  INNER JOIN merchant AS m ON t.merchant_id = m.merchant_id
  CROSS JOIN params AS x
  WHERE t.txn_date BETWEEN x.window_start AND x.as_of_date
    AND t.channel = 'CARD'
    AND t.amount < 0
    AND t.record_status_cd = 'A'
    AND m.record_status_cd = 'A'
),
spend_by_category AS (
  SELECT
    COALESCE(category, 'UNCLASSIFIED') AS category,
    COUNT(transaction_id) AS txn_count,
    SUM(-amount) AS spend
  FROM card_txn
  GROUP BY COALESCE(category, 'UNCLASSIFIED')
),
vt_card_control AS (
  SELECT m.category, COUNT(*) AS txn_count, SUM(-t.amount) AS spend
  FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
  INNER JOIN merchant AS m ON t.merchant_id = m.merchant_id
  WHERE t.channel = 'CARD' AND t.amount < 0
  GROUP BY m.category
),
control AS (
  SELECT CAST(
    CASE
      WHEN COUNT_IF(w.spend < 0 OR w.category IS NULL) > 0
        THEN 'REJECTED 09_card_category: negative spend or null category'
      WHEN COUNT_IF(s.category IS NULL OR s.spend <> w.spend OR s.txn_count <> w.txn_count) > 0
        THEN 'REJECTED 09_card_category: published totals do not reconcile'
      ELSE '0'
    END AS INT) AS rejected
  FROM vt_card_control AS w
  LEFT JOIN spend_by_category AS s ON w.category = s.category
)
SELECT
  CAST(s.category AS STRING) AS category,
  CAST(s.txn_count AS INT) AS txn_count,
  CAST(s.spend AS DECIMAL(18, 2)) AS spend,
  CAST(x.batch_id AS BIGINT) AS batch_id,
  CAST(x.load_ts AS TIMESTAMP) AS load_ts,
  CAST('COREBANK' AS STRING) AS src_system_cd,
  CAST('A' AS STRING) AS record_status_cd,
  CAST(DATE '2024-01-01' AS DATE) AS effective_date,
  CAST(DATE '9999-12-31' AS DATE) AS expiry_date
FROM spend_by_category AS s
CROSS JOIN params AS x
CROSS JOIN control AS c
WHERE c.rejected = 0;
