SELECT CASE WHEN count(*) = count(DISTINCT branch_id) AND count(*) > 0
                 AND count_if(league_rank < 1 OR net_amount IS NULL OR customer_count IS NULL) = 0
                 AND count_if(batch_id <> CAST(:batch_id AS BIGINT)) = 0
            THEN 'PASS mart_branch_performance: branch grain, ranks and batch'
            ELSE raise_error('mart_branch_performance invariant mismatch') END AS verify_grain
FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_branch_performance');

SELECT CASE WHEN count(*) = 0
            THEN 'PASS mart_branch_performance: every customer branch present with matching count'
            ELSE raise_error('mart_branch_performance does not reconcile to dim_customer') END AS verify_control
FROM (SELECT branch_id, count(*) AS customer_count
      FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') GROUP BY branch_id) AS c
LEFT JOIN IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_branch_performance') AS m
  ON c.branch_id = m.branch_id
WHERE m.branch_id IS NULL OR m.customer_count <> c.customer_count OR m.league_rank < 1;
