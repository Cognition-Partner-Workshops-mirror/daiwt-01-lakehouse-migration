SELECT CASE WHEN CAST(:as_of_date AS DATE) IS NOT NULL THEN 'ok'
            ELSE raise_error('League table requires a business date') END AS business_date_check;

SELECT CASE WHEN count(*) = 0 THEN 'ok'
            ELSE raise_error('Control failure: customer branch missing from dim_branch') END AS branch_reference_check
FROM (SELECT branch_id FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') GROUP BY branch_id) AS c
LEFT ANTI JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_branch') AS b
  ON c.branch_id = b.branch_id;

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_branch_performance')
USING DELTA
TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')
AS
WITH league AS (
  SELECT c.branch_id,
         count(DISTINCT c.customer_id) AS customer_count,
         coalesce(sum(t.amount), 0) AS net_amount,
         rank() OVER (ORDER BY coalesce(sum(t.amount), 0) DESC) AS league_rank
  FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer') AS c
  LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') AS a
    ON c.customer_id = a.customer_id AND a.record_status_cd = 'A'
  LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
    ON a.account_id = t.account_id
   AND t.txn_date BETWEEN CAST(:window_start AS DATE) AND CAST(:as_of_date AS DATE)
   AND t.record_status_cd = 'A'
  WHERE c.record_status_cd = 'A'
  GROUP BY c.branch_id
),
branch_control AS (
  SELECT branch_id, count(*) AS customer_count
  FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
  GROUP BY branch_id
),
gate AS (
  SELECT CASE
    WHEN (SELECT count(*) FROM league) <>
         (SELECT count(DISTINCT branch_id) FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_customer')
          WHERE record_status_cd = 'A')
      THEN raise_error('Branch league population is incomplete')
    WHEN EXISTS (SELECT 1 FROM branch_control AS bc LEFT JOIN league AS m ON bc.branch_id = m.branch_id
                 WHERE m.branch_id IS NULL OR m.customer_count <> bc.customer_count OR m.league_rank < 1)
      THEN raise_error('Control failure: branch league does not reconcile to customer control')
    ELSE 1 END AS ok
)
SELECT CAST(l.branch_id AS INT) AS branch_id,
       CAST(l.customer_count AS INT) AS customer_count,
       CAST(l.net_amount AS DECIMAL(18,2)) AS net_amount,
       CAST(l.league_rank AS INT) AS league_rank,
       CAST(:batch_id AS BIGINT) AS batch_id,
       CAST(concat(:as_of_date, ' 23:00:00') AS TIMESTAMP_NTZ) AS load_ts,
       'COREBANK' AS src_system_cd,
       'A' AS record_status_cd,
       DATE '2024-01-01' AS effective_date,
       DATE '9999-12-31' AS expiry_date
FROM league AS l CROSS JOIN gate AS g
WHERE g.ok = 1;
