-- Post-write verification of the published batch slice against the legacy control aggregate.
SELECT CASE WHEN count(*) > 0 AND count(*) = count(DISTINCT category)
                 AND count_if(category IS NULL OR txn_count IS NULL OR spend IS NULL OR load_ts IS NULL) = 0
                 AND count_if(src_system_cd <> 'COREBANK' OR record_status_cd <> 'A'
                              OR effective_date <> DATE '2024-01-01' OR expiry_date <> DATE '9999-12-31') = 0
            THEN 'PASS mart_card_spend_category: category grain and default columns'
            ELSE raise_error('mart_card_spend_category: grain or default column mismatch') END AS self_check
FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_card_spend_category')
WHERE batch_id = CAST(:batch_id AS BIGINT);

SELECT CASE WHEN count(*) = 0
            THEN 'PASS mart_card_spend_category: totals match control'
            ELSE raise_error('mart_card_spend_category: totals differ from control') END AS self_check
FROM (
  SELECT m.category, count(*) AS txn_count, sum(-t.amount) AS spend
  FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
  JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_merchant') AS m
    ON t.merchant_id = m.merchant_id
  WHERE t.channel = 'CARD' AND t.amount < 0
  GROUP BY m.category
) AS w
LEFT JOIN (
  SELECT category, txn_count, spend
  FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_card_spend_category')
  WHERE batch_id = CAST(:batch_id AS BIGINT)
) AS k
  ON w.category = k.category
WHERE k.category IS NULL OR k.spend <> w.spend OR k.txn_count <> w.txn_count;
