SELECT CASE WHEN (
    SELECT COUNT(*)
    FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_branch_performance')
) <> (
    SELECT COUNT(DISTINCT branch_id)
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
    WHERE upper(rtrim(record_status_cd)) = 'A'
) OR EXISTS (
    SELECT 1
    FROM (
        SELECT branch_id, COUNT(*) AS customer_count
        FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
        GROUP BY branch_id
    ) c
    LEFT JOIN IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_branch_performance') m
        ON c.branch_id = m.branch_id
    WHERE m.branch_id IS NULL OR m.customer_count <> c.customer_count
       OR m.league_rank < 1
) THEN CAST('Branch league reconciliation failed' AS INT) ELSE 1 END;
