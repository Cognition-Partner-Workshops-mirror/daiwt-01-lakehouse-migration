-- legacy/bteq/09_card_category.btq + legacy/macros/card_category.sql
-- (EXEC nvb_edw.card_category(:window_start, :as_of_date)); DDL legacy/ddl/nvb_mart.mart_card_spend_category.sql.
CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_card_spend_category') (
  category STRING COMMENT 'Business identifier used by nightly reconciliation and consumer joins.',
  txn_count INT,
  spend DECIMAL(18,2),
  batch_id BIGINT NOT NULL COMMENT 'Delivery identifier assigned by overnight operations.',
  load_ts TIMESTAMP NOT NULL COMMENT 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.',
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE
)
USING DELTA
COMMENT 'Published retail banking control summary; released after nightly reconciliation.';

-- 09_card_category.btq:26-37: control aggregate (no window or status filters); reject NULL category or negative spend.
SELECT CASE WHEN count_if(c.spend < 0 OR c.category IS NULL) = 0
            THEN 'PASS card_category: control categories and spend'
            ELSE raise_error('card_category: delivery rejected (control category NULL or spend < 0)') END AS control_check
FROM (
  SELECT m.category, count(*) AS txn_count, sum(-t.amount) AS spend
  FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
  JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_merchant') AS m
    ON t.merchant_id = m.merchant_id
  WHERE t.channel = 'CARD' AND t.amount < 0
  GROUP BY m.category
) AS c;

-- 09_card_category.btq:40-44: every control category must match the mart totals (checked before the write).
SELECT CASE WHEN count(*) = 0
            THEN 'PASS card_category: mart totals match control'
            ELSE raise_error('card_category: delivery rejected (category totals differ from control)') END AS reconcile_check
FROM (
  SELECT m.category, count(*) AS txn_count, sum(-t.amount) AS spend
  FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
  JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_merchant') AS m
    ON t.merchant_id = m.merchant_id
  WHERE t.channel = 'CARD' AND t.amount < 0
  GROUP BY m.category
) AS w
LEFT JOIN (
  -- mart candidate
  SELECT
    coalesce(m.category, 'UNCLASSIFIED') AS category,
    CAST(count(t.transaction_id) AS INT) AS txn_count,
    CAST(sum(-t.amount) AS DECIMAL(18,2)) AS spend
  FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
  INNER JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_merchant') AS m
    ON t.merchant_id = m.merchant_id
  WHERE t.txn_date BETWEEN CAST(:window_start AS DATE) AND CAST(:as_of_date AS DATE)
    AND t.channel = 'CARD'
    AND t.amount < 0
    AND t.record_status_cd = 'A'
    AND m.record_status_cd = 'A'
  GROUP BY 1
) AS k
  ON w.category = k.category
WHERE k.category IS NULL OR k.spend <> w.spend OR k.txn_count <> w.txn_count;

-- legacy/macros/card_category.sql:8-29: replace the batch slice in one commit; DDL defaults as literals.
INSERT INTO IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_card_spend_category')
REPLACE WHERE batch_id = CAST(:batch_id AS BIGINT)
SELECT
  k.category,
  k.txn_count,
  k.spend,
  CAST(:batch_id AS BIGINT) AS batch_id,
  CAST(CAST(:as_of_date AS DATE) AS TIMESTAMP) + INTERVAL 23 HOURS AS load_ts,
  'COREBANK' AS src_system_cd,
  'A' AS record_status_cd,
  DATE '2024-01-01' AS effective_date,
  DATE '9999-12-31' AS expiry_date
FROM (
  -- mart candidate
  SELECT
    coalesce(m.category, 'UNCLASSIFIED') AS category,
    CAST(count(t.transaction_id) AS INT) AS txn_count,
    CAST(sum(-t.amount) AS DECIMAL(18,2)) AS spend
  FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
  INNER JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_merchant') AS m
    ON t.merchant_id = m.merchant_id
  WHERE t.txn_date BETWEEN CAST(:window_start AS DATE) AND CAST(:as_of_date AS DATE)
    AND t.channel = 'CARD'
    AND t.amount < 0
    AND t.record_status_cd = 'A'
    AND m.record_status_cd = 'A'
  GROUP BY 1
) AS k;
