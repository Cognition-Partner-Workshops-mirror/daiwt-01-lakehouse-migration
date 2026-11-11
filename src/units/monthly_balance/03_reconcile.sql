SELECT CASE WHEN EXISTS (
    SELECT 1
    FROM (
        SELECT DISTINCT CAST(account_id AS INT) AS account_id,
            CAST(trunc(CAST(balance_date AS DATE), 'MONTH') AS DATE) AS month_start
        FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.fct_daily_balance')
    ) control
    LEFT JOIN IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_monthly_balance') m
        ON control.account_id = m.account_id AND control.month_start = m.month_start
    WHERE m.account_id IS NULL OR m.closing_balance IS NULL
) THEN CAST('Monthly balance post-write reconciliation failed' AS INT) ELSE 1 END;
