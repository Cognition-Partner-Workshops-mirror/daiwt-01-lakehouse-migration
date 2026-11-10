CREATE OR REPLACE VIEW {gold}.report_finance_monthly AS
SELECT month_start, sum(closing_balance) AS closing_balance, sum(average_balance) AS average_balance
FROM {gold}.mart_monthly_balance
WHERE year(month_start) = year(DATE '{business_date}')
GROUP BY month_start ORDER BY month_start
