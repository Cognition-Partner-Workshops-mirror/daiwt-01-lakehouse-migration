SELECT CASE WHEN COUNT(*) = 0 THEN 1
       ELSE CAST('Account customer is missing' AS INT) END
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_account') AS a
LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') AS c
  ON CAST(a.customer_id AS INT) = c.customer_id
WHERE c.customer_id IS NULL;
