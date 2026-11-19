SELECT
    category,
    spend,
    txn_count,
    spend / NULLIF(SUM(spend) OVER (), CAST(0 AS DECIMAL(28, 2))) AS share_of_debit_spend
FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_card_spend_category')
ORDER BY spend DESC;
