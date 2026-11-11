SELECT CAST(CASE WHEN COUNT(*) = 0 THEN '0'
    ELSE 'Missing calendar date in channel delivery' END AS INT) AS rejected
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
LEFT JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_calendar') AS d
    ON t.txn_date = d.calendar_date
WHERE t.record_status_cd = 'A'
    AND t.txn_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
    AND d.calendar_date IS NULL;
