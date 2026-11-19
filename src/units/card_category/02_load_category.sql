MERGE INTO IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_card_spend_category') AS target
USING (
    WITH card_debits AS (
        SELECT
            m.category,
            t.transaction_id,
            t.txn_date,
            -t.amount AS spend,
            UPPER(RTRIM(t.record_status_cd)) = 'A'
                AND UPPER(RTRIM(m.record_status_cd)) = 'A' AS is_active
        FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
        INNER JOIN IDENTIFIER(:catalog || '.' || :bronze_schema || '.dim_merchant') AS m
            ON t.merchant_id = CAST(m.merchant_id AS INT)
        WHERE m._drop = :drop
          AND UPPER(RTRIM(t.channel)) = 'CARD'
          AND t.amount < 0
    ),
    -- The control covers all joined card debits, before window and status filters.
    control_totals AS (
        SELECT RTRIM(category) AS category_key, COUNT(*) AS txn_count, SUM(spend) AS spend
        FROM card_debits
        GROUP BY RTRIM(category)
    ),
    eligible AS (
        SELECT COALESCE(category, 'UNCLASSIFIED') AS category, transaction_id, spend
        FROM card_debits
        WHERE txn_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'
          AND is_active
    ),
    delivery AS (
        SELECT
            RTRIM(category) AS category_key,
            MIN(category) AS category,
            CAST(COUNT(transaction_id) AS INT) AS txn_count,
            CAST(SUM(spend) AS DECIMAL(18, 2)) AS spend
        FROM eligible
        GROUP BY RTRIM(category)
    )
    SELECT
        d.category,
        d.txn_count,
        d.spend,
        -- An invalid control row aborts the merge before any batch is replaced.
        CASE WHEN c.category_key IS NOT NULL AND c.spend >= 0
                AND d.txn_count = c.txn_count AND d.spend = c.spend
            THEN CAST(20250630 AS BIGINT)
            ELSE CAST(CONCAT('Card spend reconciliation failed for category: ',
                COALESCE(c.category_key, '<NULL>')) AS BIGINT)
        END AS batch_id,
        TIMESTAMP '2025-06-30 23:00:00Z' AS load_ts,
        'COREBANK' AS src_system_cd,
        'A' AS record_status_cd,
        DATE '2024-01-01' AS effective_date,
        DATE '9999-12-31' AS expiry_date
    FROM control_totals AS c
    LEFT JOIN delivery AS d ON c.category_key = d.category_key
) AS source
ON FALSE
WHEN NOT MATCHED THEN INSERT *
WHEN NOT MATCHED BY SOURCE AND target.batch_id = 20250630 THEN DELETE;
