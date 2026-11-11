SELECT CASE WHEN (
    SELECT COUNT(*)
    FROM IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_customer_segment')
) <> (
    SELECT COUNT(*)
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
    WHERE upper(rtrim(record_status_cd)) = 'A'
) OR EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') c
    LEFT JOIN IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_customer_segment') s
        ON c.customer_id = s.customer_id
    WHERE upper(rtrim(c.record_status_cd)) = 'A'
      AND (s.customer_id IS NULL OR s.spend IS NULL OR s.spend < 0
           OR s.segment IS NULL OR s.segment NOT IN ('DORMANT', 'PREMIUM', 'ACTIVE', 'OCCASIONAL'))
) THEN CAST('Customer segment coverage failed' AS INT) ELSE 1 END;
