CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') AS
SELECT
    CAST(a.account_id AS INT) AS account_id,
    CAST(a.customer_id AS INT) AS customer_id,
    CAST(a.product_id AS INT) AS product_id,
    CAST(a.opened_date AS DATE) AS opened_date,
    CAST(CASE
        WHEN upper(rtrim(a.record_status_cd)) = 'A' THEN
            CASE WHEN CAST(a.customer_id AS INT) IN (
                SELECT c.customer_id
                FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') c
                JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_branch') b
                    ON c.branch_id = CAST(b.branch_id AS INT)
                WHERE upper(rtrim(b.record_status_cd)) = 'A'
            ) AND CAST(a.account_id AS INT) NOT IN (
                SELECT t.account_id
                FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') t
                WHERE t.txn_date > add_months(DATE '2025-06-30', -6)
                  AND t.txn_date <= DATE '2025-06-30'
                  AND upper(rtrim(t.record_status_cd)) = 'A'
            ) THEN 'Y' ELSE 'N' END
        ELSE a.dormant_flag
    END AS STRING) AS dormant_flag,
    CAST(a.batch_id AS BIGINT) AS batch_id,
    CAST(a.load_ts AS TIMESTAMP) AS load_ts,
    CAST(a.src_system_cd AS STRING) AS src_system_cd,
    CAST(a.record_status_cd AS STRING) AS record_status_cd,
    CAST(a.effective_date AS DATE) AS effective_date,
    CAST(a.expiry_date AS DATE) AS expiry_date,
    CAST(a.currency_cd AS STRING) AS currency_cd,
    CAST(a.account_status_cd AS STRING) AS account_status_cd,
    CAST(a.accrual_basis_cd AS STRING) AS accrual_basis_cd,
    CAST(a.overdraft_limit AS DECIMAL(18,2)) AS overdraft_limit
FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_account') a;
