-- 02_transactions: typed transaction fact for the retained window.
-- Any control failure aborts the CTAS (ANSI cast error), so nothing is written.
CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS
WITH params AS (
  SELECT
    DATE '2024-01-01' AS window_start,
    DATE '2025-06-30' AS as_of_date,
    CAST(20250630 AS BIGINT) AS batch_id,
    TIMESTAMP '2025-06-30 23:00:00' AS load_ts
),
vt_payment AS (
  SELECT
    TRY_CAST(TRIM(CAST(f.transaction_id AS STRING)) AS BIGINT) AS transaction_id,
    TRY_CAST(TRIM(CAST(f.account_id AS STRING)) AS INT) AS account_id,
    CASE
      WHEN TRIM(CAST(f.txn_date AS STRING)) RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
        THEN TRY_CAST(TRIM(CAST(f.txn_date AS STRING)) AS DATE)
    END AS txn_date,
    TRY_CAST(REPLACE(TRIM(CAST(f.amount AS STRING)), ',', '.') AS DECIMAL(18, 2)) AS amount,
    COALESCE(NULLIF(UPPER(TRIM(CAST(f.channel AS STRING))), ''), 'ONLINE') AS channel,
    TRY_CAST(TRIM(CAST(f.merchant_id AS STRING)) AS INT) AS merchant_id
  FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.transaction_feed') AS f
),
account AS (
  SELECT DISTINCT TRY_CAST(TRIM(CAST(account_id AS STRING)) AS INT) AS account_id
  FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_account')
),
merchant AS (
  SELECT DISTINCT TRY_CAST(TRIM(CAST(merchant_id AS STRING)) AS INT) AS merchant_id
  FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_merchant')
),
invalid_rows AS (
  SELECT COUNT(*) AS n
  FROM vt_payment AS p
  CROSS JOIN params AS x
  LEFT JOIN account AS a ON p.account_id = a.account_id
  LEFT JOIN merchant AS m ON p.merchant_id = m.merchant_id
  WHERE p.transaction_id IS NULL OR p.transaction_id <= 0 OR p.amount IS NULL
     OR p.txn_date IS NULL OR p.txn_date NOT BETWEEN x.window_start AND x.as_of_date
     OR a.account_id IS NULL OR m.merchant_id IS NULL
     OR p.channel NOT IN ('CARD', 'MOBILE', 'BRANCH', 'ONLINE')
),
duplicate_ids AS (
  SELECT COUNT(*) AS n
  FROM (SELECT transaction_id FROM vt_payment GROUP BY transaction_id HAVING COUNT(*) > 1) AS d
),
control AS (
  SELECT CAST(
    CASE
      WHEN i.n > 0 THEN 'REJECTED 02_transactions: invalid, out-of-window or unmatched rows'
      WHEN d.n > 0 THEN 'REJECTED 02_transactions: duplicate transaction_id'
      ELSE '0'
    END AS INT) AS rejected
  FROM invalid_rows AS i
  CROSS JOIN duplicate_ids AS d
)
SELECT
  CAST(p.transaction_id AS BIGINT) AS transaction_id,
  CAST(p.account_id AS INT) AS account_id,
  CAST(p.txn_date AS DATE) AS txn_date,
  CAST(p.amount AS DECIMAL(18, 2)) AS amount,
  CAST(p.channel AS STRING) AS channel,
  CAST(p.merchant_id AS INT) AS merchant_id,
  CAST(x.batch_id AS BIGINT) AS batch_id,
  CAST(x.load_ts AS TIMESTAMP) AS load_ts,
  CAST('COREBANK' AS STRING) AS src_system_cd,
  CAST('A' AS STRING) AS record_status_cd,
  CAST(DATE '2024-01-01' AS DATE) AS effective_date,
  CAST(DATE '9999-12-31' AS DATE) AS expiry_date,
  CAST('NOK' AS STRING) AS currency_cd,
  CAST('POSTED' AS STRING) AS posting_status_cd,
  CAST('N' AS STRING) AS reversal_ind,
  CAST('DOMESTIC' AS STRING) AS clearing_scheme_cd
FROM vt_payment AS p
CROSS JOIN params AS x
CROSS JOIN control AS c
WHERE c.rejected = 0;
