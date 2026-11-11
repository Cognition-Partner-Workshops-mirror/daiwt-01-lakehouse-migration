SELECT CASE WHEN EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
    WHERE customer_id <= 0
) THEN CAST('Invalid customer segment identifier' AS INT) ELSE 1 END;
