CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_branch_performance') AS
WITH branch_totals AS (
    SELECT
        c.branch_id,
        COUNT(DISTINCT c.customer_id) AS customer_count,
        coalesce(SUM(t.amount), 0) AS net_amount
    FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') c
    LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') a
        ON c.customer_id = a.customer_id
       AND upper(rtrim(a.record_status_cd)) = 'A'
    LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') t
        ON a.account_id = t.account_id
       AND t.txn_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
       AND upper(rtrim(t.record_status_cd)) = 'A'
    WHERE upper(rtrim(c.record_status_cd)) = 'A'
    GROUP BY c.branch_id
)
SELECT
    CAST(branch_id AS INT) AS branch_id,
    CAST(customer_count AS INT) AS customer_count,
    CAST(net_amount AS DECIMAL(18,2)) AS net_amount,
    CAST(RANK() OVER (ORDER BY net_amount DESC) AS INT) AS league_rank,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST(TIMESTAMP '2025-06-30 23:00:00' AS TIMESTAMP) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    CAST(DATE '2024-01-01' AS DATE) AS effective_date,
    CAST(DATE '9999-12-31' AS DATE) AS expiry_date
FROM branch_totals;
