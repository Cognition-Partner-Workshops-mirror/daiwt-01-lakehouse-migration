CREATE OR REPLACE VIEW {gold}.v_credit_risk AS
SELECT a.customer_id, c.customer_name, b.town AS branch_town,
       count(l.loan_id) AS loan_count, sum(l.outstanding) AS exposure,
       max(l.arrears_days) AS worst_arrears_days,
       sum(CASE WHEN l.arrears_days >= 90 THEN l.outstanding ELSE 0 END)
         / nullif(sum(l.outstanding), 0) AS impaired_ratio,
       sum(CASE WHEN l.arrears_days BETWEEN 30 AND 89 THEN l.outstanding ELSE 0 END) AS watchlist_exposure,
       CASE WHEN max(l.arrears_days) >= 90 THEN 'IMPAIRED'
            WHEN max(l.arrears_days) >= 30 THEN 'WATCH' ELSE 'PERFORMING' END AS risk_band
FROM {silver}.fct_loan AS l
JOIN {silver}.dim_account AS a ON l.account_id = a.account_id
JOIN {silver}.dim_customer AS c ON a.customer_id = c.customer_id
JOIN {silver}.dim_branch AS b ON c.branch_id = b.branch_id
WHERE l.record_status_cd = 'A'
GROUP BY a.customer_id, c.customer_name, b.town
