-- Legacy: legacy/bteq/08_channel.btq + legacy/macros/channel_daily.sql (nvb_mart.mart_channel_daily).
CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily') (
  txn_date DATE,
  channel STRING,
  txn_count INT,
  net_amount DECIMAL(18,2),
  batch_id BIGINT NOT NULL,
  load_ts TIMESTAMP NOT NULL,
  src_system_cd STRING,
  record_status_cd STRING,
  effective_date DATE,
  expiry_date DATE
)
COMMENT 'Published retail banking control summary; released after nightly reconciliation.';

CREATE OR REPLACE TEMPORARY VIEW vt_channel_control AS
SELECT txn_date, channel, count(*) AS txn_count, sum(amount) AS net_amount
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction')
GROUP BY 1, 2;

SELECT CASE WHEN count(*) = 0 THEN 'PASS every transaction date is in dim_calendar'
            ELSE raise_error('08_channel rejected: transaction date missing from dim_calendar') END AS control_check
FROM vt_channel_control AS c
LEFT JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_calendar') AS d
  ON c.txn_date = d.calendar_date
WHERE d.calendar_date IS NULL;

CREATE OR REPLACE TEMPORARY VIEW vt_channel_daily AS
SELECT
  t.txn_date,
  coalesce(nullif(trim(t.channel), ''), 'ONLINE') AS channel,
  CAST(count(*) AS INT) AS txn_count,
  CAST(coalesce(sum(t.amount), 0) AS DECIMAL(18,2)) AS net_amount
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') AS t
INNER JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_calendar') AS d
  ON t.txn_date = d.calendar_date
WHERE t.txn_date BETWEEN CAST(:window_start AS DATE) AND CAST(:as_of_date AS DATE)
  AND t.record_status_cd = 'A'
GROUP BY 1, 2;

SELECT CASE WHEN count(*) = 0 THEN 'PASS channel control reconciles'
            ELSE raise_error('08_channel rejected: channel mart does not reconcile with fct_transaction control') END AS control_check
FROM vt_channel_control AS w
LEFT JOIN vt_channel_daily AS m
  ON w.txn_date = m.txn_date AND w.channel = m.channel
WHERE m.txn_date IS NULL OR w.txn_count <> m.txn_count OR w.net_amount <> m.net_amount;

INSERT INTO IDENTIFIER(:catalog || '.' || :gold_schema || '.mart_channel_daily')
REPLACE WHERE txn_date BETWEEN CAST(:window_start AS DATE) AND CAST(:as_of_date AS DATE)
SELECT
  txn_date,
  channel,
  txn_count,
  net_amount,
  CAST(:batch_id AS BIGINT) AS batch_id,
  CAST(:load_ts AS TIMESTAMP) AS load_ts,
  'COREBANK' AS src_system_cd,
  'A' AS record_status_cd,
  DATE '2024-01-01' AS effective_date,
  DATE '9999-12-31' AS expiry_date
FROM vt_channel_daily;
