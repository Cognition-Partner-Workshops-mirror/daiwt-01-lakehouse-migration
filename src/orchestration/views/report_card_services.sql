CREATE OR REPLACE VIEW {gold}.report_card_services AS
SELECT category, txn_count, spend, spend / nullif(sum(spend) OVER (), 0) AS share_of_spend
FROM {gold}.mart_card_spend_category ORDER BY spend DESC
