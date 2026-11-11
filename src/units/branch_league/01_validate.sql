SELECT CASE WHEN EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') c
    LEFT JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_branch') b
        ON c.branch_id = CAST(b.branch_id AS INT)
    WHERE b.branch_id IS NULL
) THEN CAST('Branch league customer has no branch' AS INT) ELSE 1 END;
