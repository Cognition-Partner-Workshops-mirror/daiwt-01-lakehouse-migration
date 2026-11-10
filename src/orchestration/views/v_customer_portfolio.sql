CREATE OR REPLACE VIEW {gold}.v_customer_portfolio AS
SELECT c.customer_id, c.customer_name, c.town, b.branch_name, b.region_cd, s.segment,
       count(a.account_id) AS account_count,
       sum(CASE WHEN a.dormant_flag = 'Y' THEN 1 ELSE 0 END) AS dormant_account_count,
       count(DISTINCT a.product_id) AS product_count,
       coalesce(max(s.spend), 0) AS trailing_year_debit_spend,
       max(c.load_ts) AS customer_delivery_ts
FROM {silver}.dim_customer AS c
JOIN {silver}.dim_branch AS b ON c.branch_id = b.branch_id
LEFT JOIN {silver}.dim_account AS a ON c.customer_id = a.customer_id AND a.record_status_cd = 'A'
LEFT JOIN {gold}.mart_customer_segment AS s ON c.customer_id = s.customer_id
WHERE c.record_status_cd = 'A'
GROUP BY c.customer_id, c.customer_name, c.town, b.branch_name, b.region_cd, s.segment
