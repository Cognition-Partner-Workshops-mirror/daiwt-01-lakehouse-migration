CREATE OR REPLACE TEMPORARY VIEW monthly_balance_ledger AS
WITH parameters AS (
    SELECT DATE '2024-01-01' AS window_start, DATE '2025-06-30' AS as_of_date
),
ledger AS (
    SELECT
        CAST(account_id AS INT) AS account_id,
        CAST(balance_date AS DATE) AS balance_date,
        CAST(balance AS DECIMAL(28,6)) AS balance,
        record_status_cd
    FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.fct_daily_balance')
)
SELECT
    ledger.*,
    TRUNC(balance_date, 'MONTH') AS month_start,
    LAST_DAY(balance_date) AS month_end
FROM ledger
CROSS JOIN parameters
WHERE balance_date BETWEEN window_start AND as_of_date;

CREATE OR REPLACE TEMPORARY VIEW monthly_balance_rollup AS
SELECT
    b.account_id,
    b.month_start,
    -- Stabilize half-cent ties when DuckDB evaluates decimal AVG as a double.
    CAST(AVG(b.balance) AS DECIMAL(28,6)) AS average_balance,
    MAX(CASE WHEN b.balance_date = b.month_end THEN b.balance END) AS closing_balance
FROM monthly_balance_ledger AS b
INNER JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') AS a
    ON b.account_id = a.account_id
WHERE UPPER(RTRIM(b.record_status_cd)) = 'A'
    AND UPPER(RTRIM(a.record_status_cd)) = 'A'
GROUP BY b.account_id, b.month_start;

WITH month_control AS (
    SELECT
        account_id,
        month_start,
        month_end,
        COUNT(*) AS ledger_days,
        COUNT(DISTINCT balance_date) AS distinct_days,
        MAX(balance_date) AS latest_date
    FROM monthly_balance_ledger
    GROUP BY account_id, month_start, month_end
)
SELECT CASE WHEN COUNT(*) = 0 THEN 1
    ELSE CAST(CONCAT('Account-month ledger coverage or closing balance invalid: ',
                     CAST(COUNT(*) AS STRING)) AS INT)
    END
FROM month_control AS c
LEFT JOIN monthly_balance_rollup AS r
    ON c.account_id = r.account_id AND c.month_start = r.month_start
WHERE c.latest_date <> c.month_end
    OR c.ledger_days <> DAY(c.month_end)
    OR c.distinct_days <> c.ledger_days
    OR r.closing_balance IS NULL;

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_monthly_balance')
USING DELTA AS
SELECT
    CAST(account_id AS INT) AS account_id,
    CAST(month_start AS DATE) AS month_start,
    CAST(average_balance AS DECIMAL(18,2)) AS average_balance,
    CAST(closing_balance AS DECIMAL(18,2)) AS closing_balance,
    CAST(20250630 AS BIGINT) AS batch_id,
    CAST('2025-06-30 23:00:00' AS TIMESTAMP_NTZ) AS load_ts,
    CAST('COREBANK' AS STRING) AS src_system_cd,
    CAST('A' AS STRING) AS record_status_cd,
    DATE '2024-01-01' AS effective_date,
    DATE '9999-12-31' AS expiry_date
FROM monthly_balance_rollup;
