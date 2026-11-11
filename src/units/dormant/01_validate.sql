SELECT CASE WHEN EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_account') a
    LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') c
        ON CAST(a.customer_id AS INT) = c.customer_id
    WHERE c.customer_id IS NULL
) THEN CAST('Dormancy account has no customer' AS INT) ELSE 1 END;

SELECT CASE WHEN (
    SELECT COUNT(*)
    FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_account') a
    JOIN (
        SELECT DISTINCT customer_id, branch_id
        FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
    ) c ON c.customer_id = CAST(a.customer_id AS INT)
    JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_branch') b
        ON c.branch_id = CAST(b.branch_id AS INT)
    WHERE upper(rtrim(a.record_status_cd)) = 'A'
      AND upper(rtrim(b.record_status_cd)) = 'A'
      AND CAST(a.account_id AS INT) NOT IN (
          SELECT t.account_id
          FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') t
          WHERE t.txn_date > add_months(DATE '2025-06-30', -6)
            AND t.txn_date <= DATE '2025-06-30'
            AND upper(rtrim(t.record_status_cd)) = 'A'
      )
) > (
    SELECT COUNT(*) FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_account')
) THEN CAST('Dormancy control exceeds account population' AS INT) ELSE 1 END;
