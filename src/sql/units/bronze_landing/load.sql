SET ANSI_MODE = true;

DECLARE OR REPLACE VARIABLE _landing_batch_id BIGINT;
DECLARE OR REPLACE VARIABLE _landing_business_date DATE;
DECLARE OR REPLACE VARIABLE _landing_counts MAP<STRING, BIGINT>;
SET VAR (_landing_batch_id, _landing_business_date, _landing_counts) = (
  SELECT delivery.batch_id, delivery.business_date, delivery.counts
  FROM (
    SELECT element_at(from_json(:manifest_drops,
      'MAP<STRING, STRUCT<batch_id: BIGINT, business_date: DATE, counts: MAP<STRING, BIGINT>>>'),
      :drop) AS delivery
  )
);
SELECT CASE WHEN _landing_batch_id IS NOT NULL AND _landing_business_date IS NOT NULL
                 AND size(_landing_counts) = 11
            THEN 'Valid captured drop'
            ELSE raise_error(concat('Unknown captured drop or invalid manifest metadata: ', coalesce(:drop, '<NULL>'))) END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_account') (
  account_id INT,
  customer_id INT,
  product_id INT,
  opened_date DATE,
  dormant_flag STRING,
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP_NTZ NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  currency_cd STRING,
  account_status_cd STRING,
  accrual_basis_cd STRING,
  overdraft_limit DECIMAL(18,2),
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_account') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_account') (account_id, customer_id, product_id, opened_date, dormant_flag, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, currency_cd, account_status_cd, accrual_basis_cd, overdraft_limit, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT CAST(NULLIF(account_id, '') AS INT) AS account_id,
       CAST(NULLIF(customer_id, '') AS INT) AS customer_id,
       CAST(NULLIF(product_id, '') AS INT) AS product_id,
       CAST(NULLIF(opened_date, '') AS DATE) AS opened_date,
       COALESCE(dormant_flag, '') AS dormant_flag,
       CAST(NULLIF(batch_id, '') AS BIGINT) AS batch_id,
       CAST(NULLIF(load_ts, '') AS TIMESTAMP_NTZ) AS load_ts,
       COALESCE(src_system_cd, '') AS src_system_cd,
       COALESCE(record_status_cd, '') AS record_status_cd,
       CAST(NULLIF(effective_date, '') AS DATE) AS effective_date,
       CAST(NULLIF(expiry_date, '') AS DATE) AS expiry_date,
       COALESCE(currency_cd, '') AS currency_cd,
       COALESCE(account_status_cd, '') AS account_status_cd,
       COALESCE(accrual_basis_cd, '') AS accrual_basis_cd,
       CAST(NULLIF(overdraft_limit, '') AS DECIMAL(18,2)) AS overdraft_limit,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_edw.dim_account/nvb_edw.dim_account.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'account_id STRING, customer_id STRING, product_id STRING, opened_date STRING, dormant_flag STRING, batch_id STRING, load_ts STRING, src_system_cd STRING, record_status_cd STRING, effective_date STRING, expiry_date STRING, currency_cd STRING, account_status_cd STRING, accrual_basis_cd STRING, overdraft_limit STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('edw_dim_account: invalid CSV row') END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_branch') (
  branch_id INT,
  town STRING,
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP_NTZ NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  branch_name STRING,
  region_cd STRING,
  branch_type_cd STRING,
  service_language_cd STRING,
  branch_status_cd STRING,
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_branch') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_branch') (branch_id, town, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, branch_name, region_cd, branch_type_cd, service_language_cd, branch_status_cd, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT CAST(NULLIF(branch_id, '') AS INT) AS branch_id,
       COALESCE(town, '') AS town,
       CAST(NULLIF(batch_id, '') AS BIGINT) AS batch_id,
       CAST(NULLIF(load_ts, '') AS TIMESTAMP_NTZ) AS load_ts,
       COALESCE(src_system_cd, '') AS src_system_cd,
       COALESCE(record_status_cd, '') AS record_status_cd,
       CAST(NULLIF(effective_date, '') AS DATE) AS effective_date,
       CAST(NULLIF(expiry_date, '') AS DATE) AS expiry_date,
       COALESCE(branch_name, '') AS branch_name,
       COALESCE(region_cd, '') AS region_cd,
       COALESCE(branch_type_cd, '') AS branch_type_cd,
       COALESCE(service_language_cd, '') AS service_language_cd,
       COALESCE(branch_status_cd, '') AS branch_status_cd,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_edw.dim_branch/nvb_edw.dim_branch.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'branch_id STRING, town STRING, batch_id STRING, load_ts STRING, src_system_cd STRING, record_status_cd STRING, effective_date STRING, expiry_date STRING, branch_name STRING, region_cd STRING, branch_type_cd STRING, service_language_cd STRING, branch_status_cd STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('edw_dim_branch: invalid CSV row') END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_calendar') (
  calendar_date DATE,
  month_start DATE,
  year_no INT,
  month_no INT,
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP_NTZ NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  calendar_name STRING,
  business_day_rule_cd STRING,
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_calendar') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_calendar') (calendar_date, month_start, year_no, month_no, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, calendar_name, business_day_rule_cd, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT CAST(NULLIF(calendar_date, '') AS DATE) AS calendar_date,
       CAST(NULLIF(month_start, '') AS DATE) AS month_start,
       CAST(NULLIF(year_no, '') AS INT) AS year_no,
       CAST(NULLIF(month_no, '') AS INT) AS month_no,
       CAST(NULLIF(batch_id, '') AS BIGINT) AS batch_id,
       CAST(NULLIF(load_ts, '') AS TIMESTAMP_NTZ) AS load_ts,
       COALESCE(src_system_cd, '') AS src_system_cd,
       COALESCE(record_status_cd, '') AS record_status_cd,
       CAST(NULLIF(effective_date, '') AS DATE) AS effective_date,
       CAST(NULLIF(expiry_date, '') AS DATE) AS expiry_date,
       COALESCE(calendar_name, '') AS calendar_name,
       COALESCE(business_day_rule_cd, '') AS business_day_rule_cd,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_edw.dim_calendar/nvb_edw.dim_calendar.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'calendar_date STRING, month_start STRING, year_no STRING, month_no STRING, batch_id STRING, load_ts STRING, src_system_cd STRING, record_status_cd STRING, effective_date STRING, expiry_date STRING, calendar_name STRING, business_day_rule_cd STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('edw_dim_calendar: invalid CSV row') END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_card') (
  card_id INT,
  account_id INT,
  card_type STRING,
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP_NTZ NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  network_cd STRING,
  card_status_cd STRING,
  card_expiry_date DATE,
  contactless_ind STRING,
  online_enabled_ind STRING,
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_card') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_card') (card_id, account_id, card_type, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, network_cd, card_status_cd, card_expiry_date, contactless_ind, online_enabled_ind, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT CAST(NULLIF(card_id, '') AS INT) AS card_id,
       CAST(NULLIF(account_id, '') AS INT) AS account_id,
       COALESCE(card_type, '') AS card_type,
       CAST(NULLIF(batch_id, '') AS BIGINT) AS batch_id,
       CAST(NULLIF(load_ts, '') AS TIMESTAMP_NTZ) AS load_ts,
       COALESCE(src_system_cd, '') AS src_system_cd,
       COALESCE(record_status_cd, '') AS record_status_cd,
       CAST(NULLIF(effective_date, '') AS DATE) AS effective_date,
       CAST(NULLIF(expiry_date, '') AS DATE) AS expiry_date,
       COALESCE(network_cd, '') AS network_cd,
       COALESCE(card_status_cd, '') AS card_status_cd,
       CAST(NULLIF(card_expiry_date, '') AS DATE) AS card_expiry_date,
       COALESCE(contactless_ind, '') AS contactless_ind,
       COALESCE(online_enabled_ind, '') AS online_enabled_ind,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_edw.dim_card/nvb_edw.dim_card.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'card_id STRING, account_id STRING, card_type STRING, batch_id STRING, load_ts STRING, src_system_cd STRING, record_status_cd STRING, effective_date STRING, expiry_date STRING, network_cd STRING, card_status_cd STRING, card_expiry_date STRING, contactless_ind STRING, online_enabled_ind STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('edw_dim_card: invalid CSV row') END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_merchant') (
  merchant_id INT,
  merchant_name STRING,
  category STRING,
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP_NTZ NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  country_cd STRING,
  settlement_currency_cd STRING,
  acquirer_cd STRING,
  merchant_status_cd STRING,
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_merchant') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_merchant') (merchant_id, merchant_name, category, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, country_cd, settlement_currency_cd, acquirer_cd, merchant_status_cd, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT CAST(NULLIF(merchant_id, '') AS INT) AS merchant_id,
       COALESCE(merchant_name, '') AS merchant_name,
       COALESCE(category, '') AS category,
       CAST(NULLIF(batch_id, '') AS BIGINT) AS batch_id,
       CAST(NULLIF(load_ts, '') AS TIMESTAMP_NTZ) AS load_ts,
       COALESCE(src_system_cd, '') AS src_system_cd,
       COALESCE(record_status_cd, '') AS record_status_cd,
       CAST(NULLIF(effective_date, '') AS DATE) AS effective_date,
       CAST(NULLIF(expiry_date, '') AS DATE) AS expiry_date,
       COALESCE(country_cd, '') AS country_cd,
       COALESCE(settlement_currency_cd, '') AS settlement_currency_cd,
       COALESCE(acquirer_cd, '') AS acquirer_cd,
       COALESCE(merchant_status_cd, '') AS merchant_status_cd,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_edw.dim_merchant/nvb_edw.dim_merchant.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'merchant_id STRING, merchant_name STRING, category STRING, batch_id STRING, load_ts STRING, src_system_cd STRING, record_status_cd STRING, effective_date STRING, expiry_date STRING, country_cd STRING, settlement_currency_cd STRING, acquirer_cd STRING, merchant_status_cd STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('edw_dim_merchant: invalid CSV row') END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_product') (
  product_id INT,
  product_name STRING,
  annual_rate DECIMAL(6,2),
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP_NTZ NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  product_family_cd STRING,
  currency_cd STRING,
  product_status_cd STRING,
  rate_basis_cd STRING,
  minimum_balance DECIMAL(18,2),
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_product') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_dim_product') (product_id, product_name, annual_rate, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, product_family_cd, currency_cd, product_status_cd, rate_basis_cd, minimum_balance, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT CAST(NULLIF(product_id, '') AS INT) AS product_id,
       COALESCE(product_name, '') AS product_name,
       CAST(NULLIF(annual_rate, '') AS DECIMAL(6,2)) AS annual_rate,
       CAST(NULLIF(batch_id, '') AS BIGINT) AS batch_id,
       CAST(NULLIF(load_ts, '') AS TIMESTAMP_NTZ) AS load_ts,
       COALESCE(src_system_cd, '') AS src_system_cd,
       COALESCE(record_status_cd, '') AS record_status_cd,
       CAST(NULLIF(effective_date, '') AS DATE) AS effective_date,
       CAST(NULLIF(expiry_date, '') AS DATE) AS expiry_date,
       COALESCE(product_family_cd, '') AS product_family_cd,
       COALESCE(currency_cd, '') AS currency_cd,
       COALESCE(product_status_cd, '') AS product_status_cd,
       COALESCE(rate_basis_cd, '') AS rate_basis_cd,
       CAST(NULLIF(minimum_balance, '') AS DECIMAL(18,2)) AS minimum_balance,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_edw.dim_product/nvb_edw.dim_product.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'product_id STRING, product_name STRING, annual_rate STRING, batch_id STRING, load_ts STRING, src_system_cd STRING, record_status_cd STRING, effective_date STRING, expiry_date STRING, product_family_cd STRING, currency_cd STRING, product_status_cd STRING, rate_basis_cd STRING, minimum_balance STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('edw_dim_product: invalid CSV row') END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_daily_balance') (
  account_id INT,
  balance_date DATE,
  balance DECIMAL(18,2),
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP_NTZ NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  balance_type_cd STRING,
  currency_cd STRING,
  accrued_interest DECIMAL(18,2),
  reserved_amount DECIMAL(18,2),
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_daily_balance') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_daily_balance') (account_id, balance_date, balance, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, balance_type_cd, currency_cd, accrued_interest, reserved_amount, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT CAST(NULLIF(account_id, '') AS INT) AS account_id,
       CAST(NULLIF(balance_date, '') AS DATE) AS balance_date,
       CAST(NULLIF(balance, '') AS DECIMAL(18,2)) AS balance,
       CAST(NULLIF(batch_id, '') AS BIGINT) AS batch_id,
       CAST(NULLIF(load_ts, '') AS TIMESTAMP_NTZ) AS load_ts,
       COALESCE(src_system_cd, '') AS src_system_cd,
       COALESCE(record_status_cd, '') AS record_status_cd,
       CAST(NULLIF(effective_date, '') AS DATE) AS effective_date,
       CAST(NULLIF(expiry_date, '') AS DATE) AS expiry_date,
       COALESCE(balance_type_cd, '') AS balance_type_cd,
       COALESCE(currency_cd, '') AS currency_cd,
       CAST(NULLIF(accrued_interest, '') AS DECIMAL(18,2)) AS accrued_interest,
       CAST(NULLIF(reserved_amount, '') AS DECIMAL(18,2)) AS reserved_amount,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_edw.fct_daily_balance/nvb_edw.fct_daily_balance.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'account_id STRING, balance_date STRING, balance STRING, batch_id STRING, load_ts STRING, src_system_cd STRING, record_status_cd STRING, effective_date STRING, expiry_date STRING, balance_type_cd STRING, currency_cd STRING, accrued_interest STRING, reserved_amount STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('edw_fct_daily_balance: invalid CSV row') END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_loan') (
  loan_id INT,
  account_id INT,
  outstanding DECIMAL(18,2),
  arrears_days INT,
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP_NTZ NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  loan_status_cd STRING,
  currency_cd STRING,
  contractual_rate DECIMAL(6,2),
  repayment_frequency_cd STRING,
  collateral_type_cd STRING,
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_loan') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_loan') (loan_id, account_id, outstanding, arrears_days, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, loan_status_cd, currency_cd, contractual_rate, repayment_frequency_cd, collateral_type_cd, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT CAST(NULLIF(loan_id, '') AS INT) AS loan_id,
       CAST(NULLIF(account_id, '') AS INT) AS account_id,
       CAST(NULLIF(outstanding, '') AS DECIMAL(18,2)) AS outstanding,
       CAST(NULLIF(arrears_days, '') AS INT) AS arrears_days,
       CAST(NULLIF(batch_id, '') AS BIGINT) AS batch_id,
       CAST(NULLIF(load_ts, '') AS TIMESTAMP_NTZ) AS load_ts,
       COALESCE(src_system_cd, '') AS src_system_cd,
       COALESCE(record_status_cd, '') AS record_status_cd,
       CAST(NULLIF(effective_date, '') AS DATE) AS effective_date,
       CAST(NULLIF(expiry_date, '') AS DATE) AS expiry_date,
       COALESCE(loan_status_cd, '') AS loan_status_cd,
       COALESCE(currency_cd, '') AS currency_cd,
       CAST(NULLIF(contractual_rate, '') AS DECIMAL(6,2)) AS contractual_rate,
       COALESCE(repayment_frequency_cd, '') AS repayment_frequency_cd,
       COALESCE(collateral_type_cd, '') AS collateral_type_cd,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_edw.fct_loan/nvb_edw.fct_loan.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'loan_id STRING, account_id STRING, outstanding STRING, arrears_days STRING, batch_id STRING, load_ts STRING, src_system_cd STRING, record_status_cd STRING, effective_date STRING, expiry_date STRING, loan_status_cd STRING, currency_cd STRING, contractual_rate STRING, repayment_frequency_cd STRING, collateral_type_cd STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('edw_fct_loan: invalid CSV row') END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_product_order') (
  order_id INT,
  customer_id INT,
  product_id INT,
  order_date DATE,
  status STRING,
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP_NTZ NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  application_channel_cd STRING,
  decision_system_cd STRING,
  currency_cd STRING,
  manual_review_ind STRING,
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_product_order') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.edw_fct_product_order') (order_id, customer_id, product_id, order_date, status, batch_id, load_ts, src_system_cd, record_status_cd, effective_date, expiry_date, application_channel_cd, decision_system_cd, currency_cd, manual_review_ind, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT CAST(NULLIF(order_id, '') AS INT) AS order_id,
       CAST(NULLIF(customer_id, '') AS INT) AS customer_id,
       CAST(NULLIF(product_id, '') AS INT) AS product_id,
       CAST(NULLIF(order_date, '') AS DATE) AS order_date,
       COALESCE(status, '') AS status,
       CAST(NULLIF(batch_id, '') AS BIGINT) AS batch_id,
       CAST(NULLIF(load_ts, '') AS TIMESTAMP_NTZ) AS load_ts,
       COALESCE(src_system_cd, '') AS src_system_cd,
       COALESCE(record_status_cd, '') AS record_status_cd,
       CAST(NULLIF(effective_date, '') AS DATE) AS effective_date,
       CAST(NULLIF(expiry_date, '') AS DATE) AS expiry_date,
       COALESCE(application_channel_cd, '') AS application_channel_cd,
       COALESCE(decision_system_cd, '') AS decision_system_cd,
       COALESCE(currency_cd, '') AS currency_cd,
       COALESCE(manual_review_ind, '') AS manual_review_ind,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_edw.fct_product_order/nvb_edw.fct_product_order.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'order_id STRING, customer_id STRING, product_id STRING, order_date STRING, status STRING, batch_id STRING, load_ts STRING, src_system_cd STRING, record_status_cd STRING, effective_date STRING, expiry_date STRING, application_channel_cd STRING, decision_system_cd STRING, currency_cd STRING, manual_review_ind STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('edw_fct_product_order: invalid CSV row') END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.stg_customer_feed') (
  customer_id STRING,
  customer_name STRING,
  branch_id STRING,
  town STRING,
  updated_at STRING,
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.stg_customer_feed') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.stg_customer_feed') (customer_id, customer_name, branch_id, town, updated_at, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT COALESCE(customer_id, '') AS customer_id,
       COALESCE(customer_name, '') AS customer_name,
       COALESCE(branch_id, '') AS branch_id,
       COALESCE(town, '') AS town,
       COALESCE(updated_at, '') AS updated_at,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_stg.customer_feed/nvb_stg.customer_feed.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'customer_id STRING, customer_name STRING, branch_id STRING, town STRING, updated_at STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('stg_customer_feed: invalid CSV row') END;

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.stg_transaction_feed') (
  transaction_id STRING,
  account_id STRING,
  txn_date STRING,
  amount STRING,
  channel STRING,
  merchant_id STRING,
  _drop STRING NOT NULL,
  _batch_id BIGINT NOT NULL,
  _business_date DATE NOT NULL,
  _source_file STRING NOT NULL,
  _loaded_at TIMESTAMP NOT NULL
) USING DELTA;

DELETE FROM IDENTIFIER(:catalog || '.' || :bronze_schema || '.stg_transaction_feed') WHERE _drop = :drop;

INSERT INTO IDENTIFIER(:catalog || '.' || :bronze_schema || '.stg_transaction_feed') (transaction_id, account_id, txn_date, amount, channel, merchant_id, _drop, _batch_id, _business_date, _source_file, _loaded_at)
SELECT COALESCE(transaction_id, '') AS transaction_id,
       COALESCE(account_id, '') AS account_id,
       COALESCE(txn_date, '') AS txn_date,
       COALESCE(amount, '') AS amount,
       COALESCE(channel, '') AS channel,
       COALESCE(merchant_id, '') AS merchant_id,
       :drop, _landing_batch_id,
       _landing_business_date,
       _metadata.file_path, current_timestamp()
FROM read_files(
  :golden_path || '/inputs/' || :drop || '/nvb_stg.transaction_feed/nvb_stg.transaction_feed.csv',
  format => 'csv', header => true, sep => '|',
  schema => 'transaction_id STRING, account_id STRING, txn_date STRING, amount STRING, channel STRING, merchant_id STRING',
  nullValue => '', emptyValue => '',
  quote => '"', escape => '"', multiLine => true,
  ignoreLeadingWhiteSpace => false, ignoreTrailingWhiteSpace => false,
  mode => 'FAILFAST', enforceSchema => false,
  schemaEvolutionMode => 'rescue', rescuedDataColumn => '_rescued_data', readerCaseSensitive => true
)
WHERE CASE WHEN _rescued_data IS NULL THEN true
           ELSE raise_error('stg_transaction_feed: invalid CSV row') END;
