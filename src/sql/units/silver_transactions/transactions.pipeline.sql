-- legacy/bteq/02_transactions.btq: cleanse nvb_stg.transaction_feed, reject the whole
-- delivery on any failed control, then replace the retained window in one commit.
CREATE OR REFRESH PRIVATE MATERIALIZED VIEW transaction_delivery
AS SELECT max(_business_date) AS as_of_date,
          DATE '2024-01-01' AS window_start,
          max(_batch_id) AS batch_id
FROM ${catalog}.${bronze_schema}.stg_transaction_feed
WHERE _drop = ${catalog}.${silver_schema}.resolve_drop(:drop);

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW transaction_feed
AS SELECT transaction_id, account_id, txn_date, amount, channel, merchant_id
FROM ${catalog}.${silver_schema}.asof_stg_transaction_feed(:drop)
UNION ALL
SELECT * FROM VALUES
  ('900000001', '1', '2025-01-01', '10.00', 'CARD', '-1'),
  ('900000002', '1', '2023-12-31', '1,50', ' mobile ', '1'),
  ('-5', '1', '2025-01-01', 'abc', 'TELLER', '1')
  AS demo(transaction_id, account_id, txn_date, amount, channel, merchant_id)
WHERE '${silver_bad_rows}' = 'true';

CREATE OR REFRESH PRIVATE MATERIALIZED VIEW transaction_checked
AS WITH typed AS (
  SELECT try_cast(trim(transaction_id) AS BIGINT) AS transaction_id,
         try_cast(trim(account_id) AS INT) AS account_id,
         try_cast(trim(txn_date) AS DATE) AS txn_date,
         try_cast(replace(trim(amount), ',', '.') AS DECIMAL(18,2)) AS amount,
         coalesce(nullif(upper(trim(channel)), ''), 'ONLINE') AS channel,
         try_cast(trim(merchant_id) AS INT) AS merchant_id,
         transaction_id AS raw_transaction_id, account_id AS raw_account_id,
         txn_date AS raw_txn_date, amount AS raw_amount, channel AS raw_channel,
         merchant_id AS raw_merchant_id
  FROM transaction_feed
)
SELECT p.*,
       CASE
         WHEN p.transaction_id IS NULL OR p.transaction_id <= 0 THEN 'invalid transaction_id'
         WHEN p.amount IS NULL THEN 'invalid amount'
         WHEN p.txn_date IS NULL THEN 'invalid txn_date'
         WHEN p.txn_date NOT BETWEEN d.window_start AND d.as_of_date THEN 'txn_date outside window'
         WHEN a.account_id IS NULL THEN 'unknown account_id'
         WHEN m.merchant_id IS NULL THEN 'unknown merchant_id'
         WHEN p.channel NOT IN ('CARD', 'MOBILE', 'BRANCH', 'ONLINE') THEN 'invalid channel'
         WHEN count(*) OVER (PARTITION BY p.transaction_id) > 1 THEN 'duplicate transaction_id'
       END AS reject_reason
FROM typed AS p
CROSS JOIN transaction_delivery AS d
LEFT JOIN (SELECT DISTINCT account_id FROM ${catalog}.${silver_schema}.asof_edw_dim_account(:drop)) AS a
  ON p.account_id = a.account_id
LEFT JOIN (SELECT DISTINCT merchant_id FROM ${catalog}.${silver_schema}.dim_merchant) AS m
  ON p.merchant_id = m.merchant_id;

CREATE OR REFRESH MATERIALIZED VIEW fct_transaction (
  transaction_id BIGINT,
  account_id INT,
  txn_date DATE,
  amount DECIMAL(18,2),
  channel STRING,
  merchant_id INT,
  batch_id BIGINT,
  load_ts TIMESTAMP_NTZ,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE,
  currency_cd STRING,
  posting_status_cd STRING,
  reversal_ind STRING,
  clearing_scheme_cd STRING,
  CONSTRAINT valid_transaction_id EXPECT (transaction_id IS NOT NULL AND transaction_id > 0) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_amount EXPECT (amount IS NOT NULL) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_txn_date EXPECT (txn_date IS NOT NULL) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_channel EXPECT (channel IN ('CARD', 'MOBILE', 'BRANCH', 'ONLINE')) ON VIOLATION FAIL UPDATE,
  CONSTRAINT known_references EXPECT (account_id IS NOT NULL AND merchant_id IS NOT NULL) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_record_status EXPECT (record_status_cd IN ('A', 'D')) ON VIOLATION FAIL UPDATE
)
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT p.transaction_id, p.account_id, p.txn_date, p.amount, p.channel, p.merchant_id,
          d.batch_id,
          CAST(d.as_of_date AS TIMESTAMP_NTZ) + INTERVAL 23 HOURS AS load_ts,
          'COREBANK' AS src_system_cd,
          'A' AS record_status_cd,
          DATE '2024-01-01' AS effective_date,
          DATE '9999-12-31' AS expiry_date,
          'NOK' AS currency_cd,
          'POSTED' AS posting_status_cd,
          'N' AS reversal_ind,
          'DOMESTIC' AS clearing_scheme_cd
FROM transaction_checked AS p
CROSS JOIN transaction_delivery AS d
WHERE CASE WHEN d.as_of_date IS NULL OR d.batch_id IS NULL
             THEN raise_error('transactions: no transaction_feed delivery for drop')
           WHEN (SELECT count(*) FROM transaction_checked WHERE reject_reason IS NOT NULL) > 0
             THEN raise_error('transactions: delivery rejected by control checks; see fct_transaction_quarantine')
           ELSE true END;

CREATE OR REFRESH MATERIALIZED VIEW fct_transaction_quarantine
AS SELECT raw_transaction_id AS transaction_id, raw_account_id AS account_id,
          raw_txn_date AS txn_date, raw_amount AS amount, raw_channel AS channel,
          raw_merchant_id AS merchant_id, reject_reason
FROM transaction_checked
WHERE reject_reason IS NOT NULL;
