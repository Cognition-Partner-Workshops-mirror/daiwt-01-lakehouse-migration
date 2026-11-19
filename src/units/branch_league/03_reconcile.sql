SELECT CASE WHEN COUNT(*) = 0 THEN 1
       ELSE CAST('Branch customer count or league rank reconciliation failed' AS INT) END
FROM (
    SELECT branch_id, COUNT(*) AS customer_count
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
    GROUP BY branch_id
) AS c
LEFT JOIN IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_branch_performance') AS m
  ON c.branch_id = m.branch_id
WHERE m.branch_id IS NULL OR m.customer_count <> c.customer_count
   OR m.league_rank IS NULL OR m.league_rank < 1;

SELECT CASE WHEN
    (SELECT COUNT(*) FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_branch_performance')) =
    (SELECT COUNT(DISTINCT branch_id)
     FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
     WHERE rtrim(record_status_cd) = 'A')
    THEN 1 ELSE CAST('Branch league population is incomplete' AS INT) END;
