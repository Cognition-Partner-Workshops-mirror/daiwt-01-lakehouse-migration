CREATE OR REFRESH PRIVATE MATERIALIZED VIEW loan_candidates
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT loan_id, account_id, outstanding, arrears_days, batch_id, load_ts,
          src_system_cd, record_status_cd, effective_date, expiry_date,
          loan_status_cd, currency_cd, contractual_rate, repayment_frequency_cd,
          collateral_type_cd
FROM ${catalog}.${silver_schema}.asof_edw_fct_loan(:drop);

CREATE OR REFRESH MATERIALIZED VIEW fct_loan (
  loan_id INT,
  account_id INT,
  outstanding DECIMAL(18,2),
  arrears_days INT,
  batch_id BIGINT,
  load_ts TIMESTAMP_NTZ,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  loan_status_cd STRING,
  currency_cd STRING,
  contractual_rate DECIMAL(6,2),
  repayment_frequency_cd STRING,
  collateral_type_cd STRING,
  CONSTRAINT valid_loan EXPECT (
    batch_id IS NOT NULL AND load_ts IS NOT NULL
    AND (record_status_cd IS NULL OR record_status_cd IN ('A','D'))
    AND (contractual_rate IS NULL OR contractual_rate >= 0)
  ) ON VIOLATION DROP ROW
)
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT loan_id, account_id, outstanding, arrears_days, batch_id, load_ts,
          src_system_cd, record_status_cd, effective_date, expiry_date,
          loan_status_cd, currency_cd, contractual_rate, repayment_frequency_cd,
          collateral_type_cd
FROM loan_candidates;

CREATE OR REFRESH MATERIALIZED VIEW fct_loan_quarantine
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT loan_id, account_id, outstanding, arrears_days, batch_id, load_ts,
          src_system_cd, record_status_cd, effective_date, expiry_date,
          loan_status_cd, currency_cd, contractual_rate, repayment_frequency_cd,
          collateral_type_cd
FROM loan_candidates
WHERE NOT (
    batch_id IS NOT NULL AND load_ts IS NOT NULL
    AND (record_status_cd IS NULL OR record_status_cd IN ('A','D'))
    AND (contractual_rate IS NULL OR contractual_rate >= 0)
);
