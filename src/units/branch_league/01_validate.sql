SELECT CASE WHEN COUNT(*) = 0 THEN 1
       ELSE CAST('Branch customer control references an unknown branch' AS INT) END
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') AS c
LEFT JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_branch') AS b
  ON c.branch_id = b.branch_id
WHERE b.branch_id IS NULL;
