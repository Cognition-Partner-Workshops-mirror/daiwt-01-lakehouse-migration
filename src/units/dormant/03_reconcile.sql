SELECT CASE WHEN EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') a
    JOIN (
        SELECT w.account_id, MAX(t.txn_date) AS last_payment_date
        FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_account') w
        LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') t
            ON CAST(w.account_id AS INT) = t.account_id
        GROUP BY w.account_id, w.customer_id, w.dormant_flag
    ) control ON a.account_id = CAST(control.account_id AS INT)
    WHERE upper(rtrim(a.dormant_flag)) <> CASE
        WHEN control.last_payment_date IS NULL
          OR control.last_payment_date <= add_months(DATE '2025-06-30', -6)
        THEN 'Y' ELSE 'N' END
) THEN CAST('Dormancy post-write reconciliation failed' AS INT) ELSE 1 END;
