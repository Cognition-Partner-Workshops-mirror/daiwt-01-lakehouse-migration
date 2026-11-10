CREATE OR REPLACE VIEW {gold}.report_branch_league AS
SELECT b.town, p.customer_count, p.net_amount, p.league_rank
FROM {gold}.mart_branch_performance AS p
JOIN {silver}.dim_branch AS b ON p.branch_id = b.branch_id
ORDER BY p.league_rank
