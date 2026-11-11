-- A delivered account-month must contain a complete calendar ledger.
SELECT CASE WHEN EXISTS (
    SELECT account_id, CAST(trunc(CAST(balance_date AS DATE), 'MONTH') AS DATE) AS month_start
    FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.fct_daily_balance')
    GROUP BY account_id, CAST(trunc(CAST(balance_date AS DATE), 'MONTH') AS DATE)
    HAVING MAX(CAST(balance_date AS DATE)) <> last_day(month_start)
        OR COUNT(*) <> dayofmonth(last_day(month_start))
        OR COUNT(DISTINCT CAST(balance_date AS DATE)) <> dayofmonth(last_day(month_start))
) THEN CAST('Incomplete daily ledger month' AS INT) ELSE 1 END;

SELECT CASE WHEN EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.fct_daily_balance') b
    LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') a
        ON CAST(b.account_id AS INT) = a.account_id
    WHERE a.account_id IS NULL
) THEN CAST('Unmatched account in daily ledger' AS INT) ELSE 1 END;
