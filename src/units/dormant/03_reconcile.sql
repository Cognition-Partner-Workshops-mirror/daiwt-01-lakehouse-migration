SELECT CASE WHEN COUNT(*) = 0 THEN 1
       ELSE CAST('Account dormancy reconciliation failed' AS INT) END
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') AS a
INNER JOIN (
    SELECT CAST(w.account_id AS INT) AS account_id, MAX(t.txn_date) AS last_payment_date
    FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_account') AS w
    LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
      ON CAST(w.account_id AS INT) = t.account_id
    GROUP BY CAST(w.account_id AS INT)
) AS control ON a.account_id = control.account_id
WHERE rtrim(a.dormant_flag) <> CASE
    WHEN control.last_payment_date IS NULL
      OR control.last_payment_date <= add_months(DATE '2025-06-30', -6)
    THEN 'Y' ELSE 'N' END;

SELECT CASE WHEN
    (SELECT COUNT(*) FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account')) =
    (SELECT COUNT(*) FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_account'))
    THEN 1 ELSE CAST('Account population reconciliation failed' AS INT) END;
