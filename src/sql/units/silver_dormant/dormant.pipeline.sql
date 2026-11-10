CREATE OR REFRESH PRIVATE MATERIALIZED VIEW dormant_accounts
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT * FROM ${catalog}.${silver_schema}.asof_edw_dim_account(:drop);

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW dormant_run_params
AS SELECT CASE WHEN as_of_date IS NOT NULL THEN as_of_date
               ELSE CAST(raise_error('dormant: business date is required') AS DATE)
          END AS as_of_date,
          add_months(as_of_date, -6) AS window_start,
          CAST(date_format(as_of_date, 'yyyyMMdd') AS BIGINT) AS batch_id
FROM (
  SELECT CASE WHEN count(*) > 0 AND count(batch_id) = count(*)
                   AND count(DISTINCT batch_id) = 1
              THEN to_date(CAST(min(batch_id) AS STRING), 'yyyyMMdd')
              ELSE CAST(raise_error('dormant: exactly one non-null business date is required') AS DATE)
         END AS as_of_date
  FROM dormant_accounts
);

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW dormant_customer_control
AS SELECT CASE WHEN count(*) = 0 THEN true
               ELSE CAST(raise_error('dormant: account customer is missing') AS BOOLEAN)
          END AS customers_valid
FROM dormant_accounts a
LEFT JOIN dim_customer c ON a.customer_id = c.customer_id
WHERE c.customer_id IS NULL;

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW dormant_candidates
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT a.account_id, a.customer_id, a.product_id, a.opened_date,
  CASE WHEN a.record_status_cd = 'A' THEN
    CASE WHEN a.customer_id IN (
      SELECT c.customer_id FROM dim_customer c
      INNER JOIN dim_branch b ON c.branch_id = b.branch_id
      WHERE b.record_status_cd = 'A'
    ) AND a.account_id NOT IN (
      SELECT t.account_id FROM fct_transaction t
      WHERE t.txn_date > p.window_start AND t.txn_date <= p.as_of_date
        AND t.record_status_cd = 'A'
    ) THEN 'Y' ELSE 'N' END
    ELSE a.dormant_flag END AS dormant_flag,
  a.batch_id, a.load_ts, a.src_system_cd, a.record_status_cd,
  a.effective_date, a.expiry_date, a.currency_cd, a.account_status_cd,
  a.accrual_basis_cd, a.overdraft_limit
FROM dormant_accounts a
CROSS JOIN dormant_run_params p
CROSS JOIN dormant_customer_control v
WHERE v.customers_valid AND CASE WHEN p.as_of_date IS NOT NULL THEN true
  ELSE CAST(raise_error('dormant: business date is required') AS BOOLEAN) END;

CREATE OR REFRESH MATERIALIZED VIEW dim_account (
  account_id INT, customer_id INT, product_id INT, opened_date DATE,
  dormant_flag STRING, batch_id BIGINT, load_ts TIMESTAMP_NTZ,
  src_system_cd STRING, record_status_cd STRING, effective_date DATE,
  expiry_date DATE, currency_cd STRING, account_status_cd STRING,
  accrual_basis_cd STRING, overdraft_limit DECIMAL(18,2),
  CONSTRAINT valid_account EXPECT (account_id IS NOT NULL) ON VIOLATION DROP ROW
)
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT account_id, customer_id, product_id, opened_date, dormant_flag,
  batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date,
  currency_cd, account_status_cd, accrual_basis_cd, CAST(overdraft_limit AS DECIMAL(18,2))
FROM dormant_candidates;

CREATE OR REFRESH MATERIALIZED VIEW dim_account_quarantine
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT *, 'null account_id' AS reason_rejected
FROM dormant_candidates WHERE account_id IS NULL;
