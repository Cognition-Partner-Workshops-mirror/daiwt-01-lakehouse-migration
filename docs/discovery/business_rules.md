# DAIWT-01 Discovery: business rules and stored procedures

Scope: every business rule in the ten nightly BTEQ jobs, the three stored procedures, the three macros, the three views and the four reports of the Nordvik Sparebank Teradata 17 estate, with the exact semantics a Databricks translation must preserve, and a line-by-line comparison against `golden/compute_baselines.py` (the Python reference the D0 goldens were materialised from).

Paths are in the legacy repo (`daiwt-01-teradata-warehouse`) unless prefixed `golden:` (the UC volume `/Volumes/daiwt_01_mig_prod/landing/golden/`, capture `20261110T162154Z`, drop D0) or `mig:` (this repo).

Evidence tags:

- **[recomputed]**: re-implemented the legacy SQL semantics locally (pandas + `decimal.Decimal`) over `golden:inputs/D0/*` and compared keyed, column by column, against `golden:outputs/D0/<unit>/<table>.csv`. Counts are exact.
- **[dbsql]**: executed read-only on warehouse `6dc4b80538621b7b` (no table access, literals only).
- **[code]**: read from source; not executed.
- **[inference]**: reasoning from Teradata documentation or code shape; not verified on a Teradata system (none is available; the goldens are a Python snapshot, `golden:PROVENANCE.json` `capture_mode: snapshot`).

Target names: medallion only (`bronze`, `silver`, `gold`; `silver_pr_<n>`/`gold_pr_<n>` in dev). Legacy names `nvb_stg`/`nvb_edw`/`nvb_mart` below identify sources only.

## 0. Recompute summary (D0)

All ten contract outputs were reproduced from D0 inputs using the legacy SQL semantics. Keyed comparison, every contract column except `load_ts` and the DDL-default columns:

| unit | golden output | rows (mine / golden) | unmatched keys | mismatched cells | columns compared |
|---|---|---|---|---|---|
| customer | `golden:outputs/D0/customer/dim_customer.csv` | 5000 / 5000 | 0 | 0 | customer_name, branch_id, town, updated_at |
| transactions | `golden:outputs/D0/transactions/fct_transaction.csv` | 180000 / 180000 | 0 | 0 | account_id, txn_date, amount, channel, merchant_id |
| dormant | `golden:outputs/D0/dormant/dim_account.csv` | 8000 / 8000 | 0 | 0 | dormant_flag (105 `Y`) |
| monthly_balance | `golden:outputs/D0/monthly_balance/mart_monthly_balance.csv` | 144000 / 144000 | 0 | 0 (HALF_UP); 967 if HALF_EVEN | average_balance, closing_balance |
| customer_segment | `golden:outputs/D0/customer_segment/mart_customer_segment.csv` | 5000 / 5000 | 0 | 0 | txn_count, spend, last_txn_date, segment |
| branch_league | `golden:outputs/D0/branch_league/mart_branch_performance.csv` | 8 / 8 | 0 | 0 | customer_count, net_amount, league_rank |
| product_profitability | `golden:outputs/D0/product_profitability/mart_product_profitability.csv` | 4 / 4 | 0 | 0 | average_balance, annual_income |
| channel | `golden:outputs/D0/channel/mart_channel_daily.csv` | 2188 / 2188 | 0 | 0 | txn_count, net_amount |
| card_category | `golden:outputs/D0/card_category/mart_card_spend_category.csv` | 4 / 4 | 0 | 0 | txn_count, spend |
| close_batch | `golden:outputs/D0/close_batch/etl_batch_control.csv` | 1 / 1 | 0 | 0 | status=COMPLETE, row_count=180000, batch_id=20250630, last_step_cd=10_close_batch |

D0 input profile that bounds what D0 can prove [recomputed]:

- `nvb_stg.customer_feed`: 5100 rows, 100 superseded duplicates of `customer_id`, 0 ties on `updated_at` within a key, 0 values containing U+00A0, 0 values with leading/trailing spaces.
- `nvb_stg.transaction_feed`: 180000 rows, 0 duplicate `transaction_id`, 0 padded values, 0 empty channels, 0 comma decimals, 0 amounts with >2 decimals, channels already upper case (`MOBILE` 45116, `BRANCH` 45029, `ONLINE` 44967, `CARD` 44888), all dates within 2024-01-01..2025-06-30.
- `nvb_edw.*` inputs: all `record_status_cd = 'A'`; every account has 547 daily balances (2024-01-01..2025-06-30); 100 accounts (7901-8000) have no transactions.

Consequence: rules R-C2, R-C3, R-T2, R-T3, R-T4 (cleansing branches) and the NULL/tie branches of R-C4 are **not exercised by D0**; parity on D0 does not prove them. Wave 2 units need synthetic edge-case tests for those branches (see section 9).

## 1. Cross-cutting semantics catalogue

| # | Teradata semantic (legacy) | Databricks default | Reproducing form | Evidence |
|---|---|---|---|---|
| X-1 | `TRYCAST(TRIM(s) AS INTEGER/BIGINT/DATE/TIMESTAMP(0))` returns NULL on failure (`legacy/bteq/01_customer.btq:29,31,33`, `legacy/bteq/02_transactions.btq:29-31,34`) | `try_cast` returns NULL on failure; `try_cast(' 12 ' AS INT)` = 12 | `try_cast(trim(s) AS INT)` etc.; keep the explicit `trim` | [dbsql] |
| X-2 | `TRYCAST(OREPLACE(TRIM(amount), ',', '.') AS DECIMAL(18,2))` (`legacy/bteq/02_transactions.btq:32`) | `try_cast('1,5' AS DECIMAL(18,2))` = NULL; `try_cast('12.345' AS DECIMAL(18,2))` = 12.35 (HALF_UP) | `try_cast(replace(trim(amount), ',', '.') AS DECIMAL(18,2))` | [dbsql]; Teradata rounding of excess scale [inference] |
| X-3 | `OREPLACE(x, U+00A0, ' ')` — the search literal in `legacy/bteq/01_customer.btq:30,32` is the 2 bytes `C2 A0` (verified with `xxd`), the replacement is ASCII space `20` | `trim()` removes only ASCII space: `trim(concat(' a', char(160)))` = `'a\xa0'` (length 2) | `trim(replace(x, char(160), ' '))` — replace **before** trim, as legacy does | [code][dbsql] |
| X-4 | `TRIM` removes leading/trailing pad (space) characters only | `trim` removes spaces only | `trim(x)`; do not use regex `\s` (Python `str.strip()` at `golden/compute_baselines.py:25,27,32,41,43,44` strips all Unicode whitespace — identical on D0 because D0 has 0 padded values) | [code][recomputed] |
| X-5 | `UPPER` on UNICODE columns | `upper('æøå')` = `ÆØÅ` | `upper(x)` | [dbsql] |
| X-6 | `NOT CASESPECIFIC` comparisons are case-insensitive (`legacy/ddl/nvb_stg.transaction_feed.sql:7-12`, `legacy/ddl/nvb_edw.dim_customer.sql:8,10`, `src_system_cd` in every `legacy/ddl/*.sql`) | `'abc' = 'ABC'` is false (binary) | Normalise in the expression (legacy already applies `UPPER` to channel at `legacy/bteq/02_transactions.btq:33`); for other comparisons against literals values are already upper case in D0, so no change. Optionally `COLLATE UTF8_LCASE` on columns compared case-insensitively | [dbsql]; Teradata [inference] |
| X-7 | `CAST(decimal AS DECIMAL(18,2))` rounding: Teradata default (`RoundHalfwayMagUp` DBS Control) is banker's rounding unless the site sets it to TRUE | Spark decimal cast is HALF_UP (away from zero): `CAST(0.125 AS DECIMAL(18,2))` = 0.13, `CAST(-0.125 ...)` = -0.13; `round(2.5)`=3, `bround(2.5)`=2 | Use plain `CAST(... AS DECIMAL(18,2))` (HALF_UP): the goldens use `ROUND_HALF_UP` (`golden/compute_baselines.py:3,11-12`). Do **not** use `bround`: 967 of 144000 monthly averages would differ (section 4, R-M2) | [dbsql][recomputed]; Teradata default [inference] |
| X-8 | `AVG(CAST(x AS DECIMAL(28,6)))` | `avg(DECIMAL(28,6))` returns `DECIMAL(32,10)` | `CAST(avg(CAST(x AS DECIMAL(28,6))) AS DECIMAL(18,2))` — both a 6-dp and a 10-dp intermediate reproduce the golden exactly on D0 (0/144000 and 0/4 mismatches) | [dbsql][recomputed] |
| X-9 | `DECIMAL * DECIMAL / 100` keeps DECIMAL | `DECIMAL(32,10) * DECIMAL(6,2) / 100` → `DECIMAL(38,11)` | keep the whole expression DECIMAL, cast once at the end | [dbsql][recomputed] |
| X-10 | Golden numeric CSV values are printed from Python floats rounded HALF_UP to cents (`golden/compute_baselines.py:11-12,50`) | `CAST(double AS DECIMAL)` rounds the binary value | Never round a DOUBLE directly; if a float appears, use `CAST(CAST(x AS STRING) AS DECIMAL(p,s))` (`CAST(CAST(0.1D+0.2D AS STRING) AS DECIMAL(18,2))` = 0.30). Silver keeps DECIMAL(18,2) end to end so no float path is needed; float summation of `amount` would drift up to 1E-11 per channel-day before rounding (measured) | [dbsql][recomputed] |
| X-11 | `ZEROIFNULL(x)` | n/a | `coalesce(x, 0)` (typed: `coalesce(x, CAST(0 AS DECIMAL(18,2)))`) | [code] |
| X-12 | `NULLIFZERO(x)` | n/a | `nullif(x, 0)`; division by NULL → NULL. Use `try_divide` only if ANSI mode is on and a zero could survive | [code] |
| X-13 | `ADD_MONTHS(d, n)`: keeps day-of-month, clamps to last day of target month; an end-of-month input is **not** pushed to end of month (`ADD_MONTHS(2025-06-30,-6)` = 2024-12-30) | `add_months(DATE'2025-06-30',-6)` = 2024-12-30; `add_months(DATE'2024-02-29',12)` = 2025-02-28; `add_months(DATE'2025-02-28',1)` = 2025-03-28 | `add_months` (same semantics). Runbook confirms the first recent dormancy date is 2024-12-31 (`legacy/schedules/runbook.md:48-50`), consistent with `> 2024-12-30` | [dbsql][code]; Teradata [inference] |
| X-14 | `DATE - 90` (integer days) | `DATE'2025-06-30' - 90` = `date_sub(...,90)` = 2025-04-01 | `date_sub(as_of_date, 90)` | [dbsql] |
| X-15 | `BETWEEN a AND b` inclusive both ends | same | `BETWEEN` | [code] |
| X-16 | `TRUNC(d, 'MONTH')` returns DATE | `trunc(DATE'2025-06-17','MONTH')` = 2025-06-01, type `date` | `trunc(d, 'MONTH')` | [dbsql] |
| X-17 | `ADD_MONTHS(TRUNC(d,'MONTH'),1) - 1` = month end | `last_day(d)` (`last_day(DATE'2024-02-10')` = 2024-02-29) | `last_day(d)` | [dbsql] |
| X-18 | `QUALIFY ROW_NUMBER() OVER (...) = 1` | Databricks supports `QUALIFY` | identical; write `NULLS` placement explicitly (Teradata sorts NULL low: DESC → NULLS LAST) | [code]; NULL order [inference] |
| X-19 | `RANK() OVER (ORDER BY x DESC)` ties share rank, gaps follow | same | `rank() OVER (ORDER BY x DESC)` | [code][recomputed] |
| X-20 | `CSUM(x, sort)` running sum, no partition | n/a | `sum(x) OVER (ORDER BY sort ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)` | [code]; [inference] |
| X-21 | `MAVG(x, 7, sort)` moving average of current + 6 preceding rows (fewer rows at the start) | n/a | `avg(x) OVER (ORDER BY sort ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)` | [code]; [inference] |
| X-22 | `HASHROW(...)` (`legacy/bteq/01_customer.btq:34`) | no equivalent; value is not persisted (not in MERGE at :62-71) | drop | [code] |
| X-23 | `SET TABLE` silently discards full-row duplicates on INSERT-SELECT (all dims, marts and `etl_batch_control`: line 1 of each `legacy/ddl/nvb_edw.dim_*.sql`, `legacy/ddl/nvb_mart.*.sql`, `legacy/ddl/nvb_edw.etl_batch_control.sql`); `MULTISET` keeps them (`legacy/ddl/nvb_edw.fct_*.sql:1`, `legacy/ddl/nvb_stg.*.sql:1`) | Delta keeps duplicates | Silver dims/marts are produced by GROUP BY / dedup / MERGE on their unique key, so no full-row duplicate can arise (D0: 0); assert key uniqueness in tests rather than emulate SET | [code][recomputed] |
| X-24 | `UNIQUE PRIMARY INDEX` (dims) / `PRIMARY INDEX` (marts, facts), `PARTITION BY RANGE_N` (`legacy/ddl/nvb_edw.fct_transaction.sql:24-25`, `legacy/ddl/nvb_edw.fct_daily_balance.sql:21-22`), `FALLBACK`, `CHECKSUM`, `COMPRESS (...)`, `COLLECT STATISTICS` | no semantic effect | drop; optional liquid clustering on the same keys. Keep `CHECK (record_status_cd IN ('A','D'))` (e.g. `legacy/ddl/nvb_edw.dim_customer.sql:15`) as a Delta CHECK constraint or expectation | [code] |
| X-25 | `CREATE VOLATILE TABLE ... ON COMMIT PRESERVE ROWS` session scratch (every BTEQ job, e.g. `legacy/bteq/01_customer.btq:26-36`) | n/a | CTEs / temp views inside the unit; never persisted | [code] |
| X-26 | `BT; ... ET;` and macro bodies are single transactions (`legacy/bteq/02_transactions.btq:53-64`, `legacy/bteq/07_product_profitability.btq:43-53`, `legacy/schedules/runbook.md:43`) | Delta commits per statement | Use a single `INSERT OVERWRITE`/`REPLACE WHERE` or `CREATE OR REPLACE TABLE` per output so delete+insert is atomic | [code] |
| X-27 | `SET SESSION DATEFORM = ANSIDATE` (line 6 of each BTEQ) and `CAST(txn_date AS DATE FORMAT 'YYYY-MM-DD')` (`legacy/bteq/02_transactions.btq:60`) | ISO dates | none | [code] |

## 2. Hardcoded literals and parameterisation

| literal | occurrences | meaning | translation |
|---|---|---|---|
| `DATE '2025-06-30'` | business date in every BTEQ batch-control statement (e.g. `legacy/bteq/01_customer.btq:10,17,22,84`), procedure calls (`legacy/bteq/03_dormant.btq:40`, `05_customer_segment.btq:37`, `06_branch_league.btq:37`), macro calls (`04_monthly_balance.btq:40`, `08_channel.btq:38`, `09_card_category.btq:38`), window end (`02_transactions.btq:43,56`, `07_product_profitability.btq:35`), control (`03_dormant.btq:47`), views (`legacy/views/v_channel_trend.sql:21`), `legacy/schedules/nightly.yaml:2`, `golden/compute_baselines.py:46,71,87,106`, DDL defaults (`legacy/ddl/nvb_edw.etl_batch_control.sql:19`) | control/business date (`as_of_date`) | job parameter `as_of_date` (default `2025-06-30` for D0) |
| `DATE '2024-01-01'` | retained window start: `02_transactions.btq:43,56`, `07_product_profitability.btq:35`, `legacy/procs/branch_league.sql:38`, macro calls `04:40`, `08:38`, `09:38`, `v_channel_trend.sql:21`, DDL `effective_date` defaults (e.g. `legacy/ddl/nvb_edw.dim_customer.sql:16`), `etl_batch_control.sql:18`, `golden/compute_baselines.py:46` | retention window start | parameter `window_start_date` (default 2024-01-01); `effective_date`/`window_start_date` columns are DDL defaults (section 7) |
| `20250630` | `batch_id` in MERGE (`01_customer.btq:67`), mart deletes (`legacy/procs/branch_league.sql:20`, `legacy/procs/customer_segment.sql:20`, `legacy/macros/card_category.sql:8-9`, `07_product_profitability.btq:45`), every DDL `batch_id` default (e.g. `legacy/ddl/nvb_mart.mart_monthly_balance.sql:11`), `data/column_defaults.json` | delivery batch identifier, `yyyymmdd` of the business date | derive `CAST(date_format(as_of_date,'yyyyMMdd') AS BIGINT)`; must stay constant on same-delivery restart (`legacy/schedules/runbook.md:38-40`) |
| `TIMESTAMP '2025-06-30 23:00:00'` | `load_ts` in MERGE (`01_customer.btq:67`) and DDL defaults (e.g. `legacy/ddl/nvb_edw.dim_customer.sql:13`) | logical load timestamp | parameter; excluded from parity (`golden:manifest.json` `excluded_from_parity`) |
| `-6` months, `-12` months `+1`, `90` days, `50`/`20000`/`20` | `legacy/procs/flag_dormant.sql:40`, `legacy/procs/customer_segment.sql:31,33-36,44` | dormancy / segmentation thresholds | keep as literals in the unit (business constants), derived from `as_of_date` |
| `'2025-04-01'`, `'2024-07-01'`, `'2024-12-31'` | Python only: `golden/compute_baselines.py:71,87,91` | the reference hardcodes the values the SQL derives | derive from `as_of_date` as the SQL does (identical at D0, section 8) |
| `EXTRACT(YEAR FROM month_start)=2025` | `reports/finance_monthly.sql:4` | report filter | parameter `report_year` or `year(as_of_date)` in the business view |
| `'ONLINE'`, `('CARD','MOBILE','BRANCH','ONLINE')`, `'UNCLASSIFIED'`, `'MOBILE'` | `02_transactions.btq:33,45`, `legacy/macros/channel_daily.sql:20`, `legacy/macros/card_category.sql:18`, `legacy/views/v_channel_trend.sql:7,20` | domain values | literals |

## 3. Silver rules

### customer → `silver.dim_customer` (job 01, `legacy/bteq/01_customer.btq`)

| id | rule (semantics to preserve) | source | Databricks SQL | golden columns | D0 |
|---|---|---|---|---|---|
| R-C1 | IDs: `TRYCAST(TRIM(customer_id) AS INTEGER)`, `TRYCAST(TRIM(branch_id) AS INTEGER)` | `:29,31` | `try_cast(trim(customer_id) AS INT)` | customer_id, branch_id | [recomputed] 0 mismatches |
| R-C2 | Name/town: replace U+00A0 (bytes `C2 A0`) with space, then TRIM | `:30,32` | `trim(replace(customer_name, char(160), ' '))` | customer_name, town | [recomputed] 0 mismatches; branch not exercised (0 NBSP in D0) |
| R-C3 | `updated_at`: `TRYCAST(TRIM(updated_at) AS TIMESTAMP(0))` (feed format `YYYY-MM-DD HH:MM:SS`) | `:33`; Python `golden/compute_baselines.py:32-34` | `try_to_timestamp(trim(updated_at), 'yyyy-MM-dd HH:mm:ss')` | updated_at (golden prints ISO `...T..:..:..000Z`) | [recomputed] 0 mismatches |
| R-C4 | Delivery is rejected (job aborts, return code 12) if any row has NULL or ≤0 `customer_id`, NULL or empty name, NULL `updated_at`, or a `branch_id` not in `dim_branch` | `:39-44`; `legacy/schedules/runbook.md:34-35`; Python `:28-34` | unit-level assertion (`raise_error` / expectation with fail) evaluated **before** writing | — | [recomputed] D0 passes (0 violations) |
| R-C5 | Dedup: one row per `customer_id`, `ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY updated_at DESC, customer_name DESC, town DESC, branch_id DESC) = 1` | `:48-57` | `QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY updated_at DESC, customer_name DESC, town DESC, branch_id DESC) = 1` | all | [recomputed] 5100 → 5000, 0 mismatches; 100 superseded rows dropped; 0 ties |
| R-C6 | MERGE on `customer_id`: matched rows update name, branch, town, updated_at **and** set `batch_id = 20250630`, `load_ts`; inserted rows set only the five business columns, so `batch_id`, `load_ts`, `src_system_cd`, `record_status_cd`, `effective_date`, `expiry_date`, `customer_type_cd`, … come from DDL defaults | `:62-71`; `legacy/ddl/nvb_edw.dim_customer.sql:12-22` | Silver is rebuilt from the delivery each run; emit the defaults explicitly (section 7). Matched-row columns other than the five + batch_id/load_ts keep their prior values | business columns + default columns | [recomputed] business columns 0 mismatches; defaults equal `data/column_defaults.json` |
| R-C7 | Batch control: row inserted as `OPEN` if absent, set `RUNNING` with `last_step_cd`, set `FAILED` on error | `:8-22,82-84` | orchestration state (Wave 1); not a parity column except via job 10 | etl_batch_control | [code] |

### transactions → `silver.fct_transaction` (job 02, `legacy/bteq/02_transactions.btq`)

| id | rule | source | Databricks SQL | golden columns | D0 |
|---|---|---|---|---|---|
| R-T1 | IDs and date: `TRYCAST(TRIM(x) AS BIGINT/INTEGER/DATE)` | `:29-31,34` | `try_cast(trim(x) AS BIGINT)` / `INT` / `DATE` | transaction_id, account_id, merchant_id, txn_date | [recomputed] 0 |
| R-T2 | Amount: `TRYCAST(OREPLACE(TRIM(amount), ',', '.') AS DECIMAL(18,2))` — comma decimal separator accepted | `:32` | `try_cast(replace(trim(amount), ',', '.') AS DECIMAL(18,2))` | amount | [recomputed] 0; comma branch not exercised |
| R-T3 | Channel: `COALESCE(NULLIF(UPPER(TRIM(channel)), ''), 'ONLINE')` — blank/NULL defaults to `ONLINE` | `:33` | `coalesce(nullif(upper(trim(channel)), ''), 'ONLINE')` | channel | [recomputed] 0; default branch not exercised |
| R-T4 | Reject delivery if any `transaction_id` NULL/≤0, amount NULL, date NULL or outside 2024-01-01..2025-06-30 (inclusive), account not in `dim_account`, merchant not in `dim_merchant`, or channel not in (`CARD`,`MOBILE`,`BRANCH`,`ONLINE`); reject on any duplicate `transaction_id` | `:38-45,48` | pre-write assertions | — | [recomputed] D0 passes |
| R-T5 | Replace window: `DELETE ... WHERE txn_date BETWEEN 2024-01-01 AND 2025-06-30` then insert, in one transaction | `:53-64` | `INSERT INTO ... REPLACE WHERE txn_date BETWEEN :window_start AND :as_of_date` or full overwrite | all | [code] |
| R-T6 | Inserted columns are the six business columns; the rest (`batch_id`, `record_status_cd`, `currency_cd`, `posting_status_cd`, `reversal_ind`, `clearing_scheme_cd`, …) are DDL defaults | `:58-62`; `legacy/ddl/nvb_edw.fct_transaction.sql:13-22` | literal defaults (section 7) | default columns | [recomputed] |

### dormant → `silver.dim_account` (job 03, `legacy/bteq/03_dormant.btq` + `legacy/procs/flag_dormant.sql`)

| id | rule | source | Databricks SQL | golden columns | D0 |
|---|---|---|---|---|---|
| R-D1 | Reject if any account's `customer_id` is not in `dim_customer` | `03_dormant.btq:35-37` | assertion | — | [recomputed] passes |
| R-D2 | Reset all active accounts to `dormant_flag = 'N'` | `flag_dormant.sql:19-21` | default `'N'` in the CASE | dormant_flag | [recomputed] |
| R-D3 | Set `'Y'` for active accounts of customers of each active branch (cursor over `dim_branch` ordered by `branch_id`) whose `account_id` is `NOT IN` the set of active transactions with `txn_date > ADD_MONTHS(as_of_date, -6) AND txn_date <= as_of_date` | `flag_dormant.sql:23-46`, cutoff `:40-41` | set-based: `CASE WHEN a.record_status_cd='A' AND c.branch_id IN (active branches) AND NOT EXISTS (SELECT 1 FROM fct_transaction t WHERE t.account_id=a.account_id AND t.txn_date > add_months(:as_of_date,-6) AND t.txn_date <= :as_of_date AND t.record_status_cd='A') THEN 'Y' ELSE 'N' END`. Use `NOT EXISTS` only if `fct_transaction.account_id` is non-null (guaranteed by R-T4); `NOT IN` against a set containing NULL yields no `Y` in both engines | dormant_flag | [recomputed] 0/8000 mismatches; 105 `Y` (100 never-transacted + 5) |
| R-D4 | First recent date is 2024-12-31 at the D0 date; boundary is `ADD_MONTHS`, not 180 days | `legacy/schedules/runbook.md:48-50` | `add_months` (X-13) | dormant_flag | [recomputed] a 180-day cutoff (`> 2025-01-01`) and a 2024-12-31 strict cutoff also give 0 differences on D0, so D0 does not discriminate the boundary — unit tests must |
| R-D5 | Post-check: `dormant_flag` must equal `CASE WHEN last_payment_date IS NULL OR last_payment_date <= ADD_MONTHS(2025-06-30,-6) THEN 'Y' ELSE 'N' END` (last payment over all dates) ; total flagged ≤ account count | `03_dormant.btq:42-48`; `flag_dormant.sql:48-51` | test assertion | dormant_flag | [recomputed] passes |

### close_batch → `silver.etl_batch_control` (job 10, `legacy/bteq/10_close_batch.btq`)

| id | rule | source | Databricks SQL | golden columns | D0 |
|---|---|---|---|---|---|
| R-B1 | Reconcile `COUNT(*)`, `SUM(amount)` of `fct_transaction` with `SUM(txn_count)`, `SUM(net_amount)` of `mart_channel_daily`; fail if different or mart empty | `:26-40` | assertion | — | [recomputed] 180000 = 180000, sums equal |
| R-B2 | Final row: `status='COMPLETE'`, `last_step_cd='10_close_batch'`, `row_count` = fact count, keyed by `(batch_date, job_name='NIGHTLY')` | `:43-46` | single-row MERGE/overwrite | batch_date, job_name, status, row_count, last_step_cd | [recomputed] 0 |
| R-B3 | Other columns from DDL defaults: `batch_id 20250630`, `src_system_cd 'COREBANK'`, `record_status_cd 'A'`, `effective_date 2024-01-01`, `expiry_date 9999-12-31`, `window_start_date 2024-01-01`, `window_end_date 2025-06-30`, `reject_count 0`, `scheduler_group_cd 'NVB_NIGHTLY'` | `legacy/ddl/nvb_edw.etl_batch_control.sql:11-21`; `data/column_defaults.json` | literals / parameters | listed | [recomputed] 0 |

## 4. Gold rules

### monthly_balance → `gold.mart_monthly_balance` (job 04 + `legacy/macros/monthly_balance.sql`)

| id | rule | source | Databricks SQL | golden columns | D0 |
|---|---|---|---|---|---|
| R-M1 | Reject if any account-month lacks the month-end day or has `ledger_days <> day(month end)` | `04_monthly_balance.btq:26-37`; `legacy/schedules/runbook.md:51-52` | assertion | — | [recomputed] passes (547 days per account) |
| R-M2 | `average_balance = CAST(AVG(CAST(balance AS DECIMAL(28,6))) AS DECIMAL(18,2))` per `(account_id, TRUNC(balance_date,'MONTH'))` | `monthly_balance.sql:18-21` | `CAST(avg(CAST(balance AS DECIMAL(28,6))) AS DECIMAL(18,2))` (HALF_UP) | average_balance | [recomputed] 0/144000. 1894 rows have an exact half-cent mean; under HALF_EVEN 967 would differ |
| R-M3 | `closing_balance = MAX(CASE WHEN balance_date = ADD_MONTHS(TRUNC(balance_date,'MONTH'),1)-1 THEN balance END)` — NULL if month end missing (prevented by R-M1) | `monthly_balance.sql:22-25` | `max(CASE WHEN balance_date = last_day(balance_date) THEN balance END)` | closing_balance | [recomputed] 0 |
| R-M4 | Inner join to active `dim_account`; active balances; `balance_date BETWEEN :p_from AND :p_to` (called with 2024-01-01, 2025-06-30); delete-then-insert of `month_start BETWEEN TRUNC(:p_from) AND TRUNC(:p_to)` | `monthly_balance.sql:8-9,26-31`; `04_monthly_balance.btq:40` | `REPLACE WHERE month_start BETWEEN trunc(:from,'MONTH') AND trunc(:to,'MONTH')` | all | [recomputed] 144000 = 8000 × 18 |
| R-M5 | Post-check: every control account-month present with non-NULL closing | `04_monthly_balance.btq:42-45` | test | — | [recomputed] passes |

### customer_segment → `gold.mart_customer_segment` (job 05 + `legacy/procs/customer_segment.sql`)

| id | rule | source | Databricks SQL | golden columns | D0 |
|---|---|---|---|---|---|
| R-S1 | Population: every active customer (LEFT JOIN active accounts, LEFT JOIN active transactions with `txn_date BETWEEN ADD_MONTHS(as_of_date,-12)+1 AND as_of_date` = 2024-07-01..2025-06-30) | `customer_segment.sql:39-47` | `LEFT JOIN ... ON ... AND t.txn_date BETWEEN date_add(add_months(:as_of,-12),1) AND :as_of` (window predicate stays in ON) | customer_id | [recomputed] 5000 |
| R-S2 | `txn_count = COUNT(t.transaction_id)` (0 when none) | `:27` | `count(t.transaction_id)` | txn_count | [recomputed] 0 |
| R-S3 | `spend = ZEROIFNULL(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END))` (debits only, positive) | `:28` | `coalesce(sum(CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END), 0)` | spend | [recomputed] 0 |
| R-S4 | `last_txn_date = MAX(t.txn_date)` (NULL when none) | `:29` | `max(t.txn_date)` | last_txn_date | [recomputed] 0 (no NULL case in D0) |
| R-S5 | Segment, first match wins: `DORMANT` if last date NULL or `< as_of_date - 90` (2025-04-01, strict); `PREMIUM` if count ≥ 50 and un-ZEROIFNULLed spend ≥ 20000; `ACTIVE` if count ≥ 20; else `OCCASIONAL` | `:30-38`; `legacy/schedules/runbook.md:50-51` | same `CASE`, `date_sub(:as_of, 90)` | segment | [recomputed] 0; DORMANT 61, PREMIUM 221, ACTIVE 2327, OCCASIONAL 2391. Boundaries present: 3 customers with last date = 2025-04-01 (not DORMANT), 1 with 2025-03-31 (DORMANT), 181 with count exactly 20 or 50 |
| R-S6 | Coverage: rows inserted = active customers, else fail; post-check spend ≥ 0 and segment in the 4 values | `:17-18,48-52`; `05_customer_segment.btq:39-43` | test | — | [recomputed] passes |
| R-S7 | Delete by `batch_id = 20250630` then insert | `:20` | overwrite of the batch slice (section 2) | — | [code] |

### branch_league → `gold.mart_branch_performance` (job 06 + `legacy/procs/branch_league.sql`)

| id | rule | source | Databricks SQL | golden columns | D0 |
|---|---|---|---|---|---|
| R-L1 | Group active customers by `branch_id`; `customer_count = COUNT(DISTINCT customer_id)` | `branch_league.sql:28-30,40-41` | `count(DISTINCT c.customer_id)` | customer_count | [recomputed] 0 |
| R-L2 | `net_amount = ZEROIFNULL(SUM(t.amount))` over active accounts (LEFT) and active transactions (LEFT) with `txn_date BETWEEN 2024-01-01 AND as_of_date`; signed (credits and debits net) | `:31,34-39` | `coalesce(sum(t.amount), 0)` with the window in the ON clause | net_amount | [recomputed] 0 |
| R-L3 | `league_rank = RANK() OVER (ORDER BY ZEROIFNULL(SUM(t.amount)) DESC)` | `:32` | `rank() OVER (ORDER BY coalesce(sum(t.amount),0) DESC)` | league_rank | [recomputed] 0; no ties in D0 (ties would share rank) |
| R-L4 | Row count must equal distinct active branches among customers; post-check every branch present with matching count, rank ≥ 1 | `:17-18,44-46`; `06_branch_league.btq:32-34,39-43` | test | — | [recomputed] passes (8) |

### product_profitability → `gold.mart_product_profitability` (job 07, `legacy/bteq/07_product_profitability.btq`)

| id | rule | source | Databricks SQL | golden columns | D0 |
|---|---|---|---|---|---|
| R-P1 | `precise_balance = AVG(CAST(balance AS DECIMAL(28,6)))` over all active account-day balances of the product in 2024-01-01..2025-06-30 (one mean over account-days, not a mean of account means) | `:26-37` | `avg(CAST(b.balance AS DECIMAL(28,6)))` grouped by `product_id` | — | [recomputed] |
| R-P2 | `annual_rate = MAX(p.annual_rate)`; reject if rate NULL/negative or balance NULL | `:30,39-40` | assertion | — | [recomputed] passes |
| R-P3 | `average_balance = CAST(precise_balance AS DECIMAL(18,2))`; `annual_income = CAST(precise_balance * annual_rate / 100 AS DECIMAL(18,2))` | `:49-50` | `CAST(avg_b AS DECIMAL(18,2))`, `CAST(avg_b * annual_rate / 100 AS DECIMAL(18,2))` (avg_b unrounded) | average_balance, annual_income | [recomputed] 0/4 (values 397757.71/5966.37, 397934.80/13927.72, 397645.43/20876.38, 397786.91/79159.60) for exact, 6-dp and 10-dp intermediates |
| R-P4 | Delete `batch_id = 20250630`, insert, in one transaction | `:43-53` | overwrite | — | [code] |

### channel → `gold.mart_channel_daily` (job 08 + `legacy/macros/channel_daily.sql`)

| id | rule | source | Databricks SQL | golden columns | D0 |
|---|---|---|---|---|---|
| R-H1 | Reject if any transaction date is missing from `dim_calendar` | `08_channel.btq:33-35` | assertion | — | [recomputed] passes |
| R-H2 | Group by `txn_date`, `COALESCE(NULLIF(TRIM(channel),''),'ONLINE')` (TRIM, no UPPER) over active transactions inner-joined to `dim_calendar`, `txn_date BETWEEN :p_from AND :p_to`; `txn_count = COUNT(*)`, `net_amount = ZEROIFNULL(SUM(amount))` | `channel_daily.sql:18-28` | same | txn_date, channel, txn_count, net_amount | [recomputed] 0/2188 |
| R-H3 | Delete `txn_date BETWEEN :p_from AND :p_to` then insert | `channel_daily.sql:8-9` | `REPLACE WHERE` | — | [code] |
| R-H4 | Post-check equality with a control aggregation over all of `fct_transaction`; report average = `SUM(net_amount)/NULLIFZERO(SUM(txn_count))` | `08_channel.btq:40-50` | test / business view | — | [recomputed] passes |

### card_category → `gold.mart_card_spend_category` (job 09 + `legacy/macros/card_category.sql`)

| id | rule | source | Databricks SQL | golden columns | D0 |
|---|---|---|---|---|---|
| R-K1 | Active `CARD` transactions with `amount < 0`, **inner** join to active `dim_merchant`, window `:p_from..:p_to`; `category = COALESCE(m.category,'UNCLASSIFIED')` (reachable only for a merchant row with NULL category); `txn_count = COUNT(transaction_id)`, `spend = SUM(-amount)` | `card_category.sql:17-29` | same join type and predicates | category, txn_count, spend | [recomputed] 0/4; 0 NULL categories; 26157 CARD credits (amount > 0) excluded by the sign predicate |
| R-K2 | Delete `batch_id = 20250630` then insert | `:8-9` | overwrite | — | [code] |
| R-K3 | Control: same aggregate without status filters; reject NULL category or negative spend; report share `spend / NULLIFZERO(SUM(spend) OVER ())` | `09_card_category.btq:26-49` | business view `spend / nullif(sum(spend) OVER (), 0)` | — | [recomputed] passes |

## 5. Views (Wave 3 business views)

No golden output exists for the views (`mig:src/parity/contract.json` lists tables only), so these are [code] only.

| view | rules | source | Databricks SQL |
|---|---|---|---|
| `v_channel_trend` | Calendar spine 2024-01-01..2025-06-30 LEFT JOIN `mart_channel_daily` for `channel='MOBILE'` (filter in ON); `txn_count`, `net_amount` ZEROIFNULL; `running_amount = CSUM(ZEROIFNULL(net_amount), calendar_date)`; `seven_day_average = MAVG(ZEROIFNULL(net_amount), 7, calendar_date)`; `average_payment = ZEROIFNULL(net_amount)/NULLIFZERO(txn_count)` (NULL on no-activity days); `activity_status` `NO_ACTIVITY` when no mart row else `CLEARED` | `legacy/views/v_channel_trend.sql:1-21` | X-20, X-21, X-12; window literals → parameters; the division's result scale differs between engines [inference] — cast explicitly to the reconciled type |
| `v_credit_risk` | INNER joins loan→account→customer→branch, active loans; `loan_count`, `exposure = SUM(outstanding)`, `worst_arrears_days = MAX`, `impaired_ratio = SUM(outstanding where arrears ≥ 90)/NULLIFZERO(SUM(outstanding))`, `watchlist_exposure` = arrears BETWEEN 30 AND 89, `risk_band` IMPAIRED ≥ 90 / WATCH ≥ 30 / PERFORMING | `legacy/views/v_credit_risk.sql:1-23` | same; needs `silver.fct_loan`, `silver.dim_branch` (not contract outputs) |
| `v_customer_portfolio` | Active customers INNER JOIN branch, LEFT JOIN active accounts, LEFT JOIN segment mart; `account_count`, `dormant_account_count`, `product_count = COUNT(DISTINCT product_id)`, `trailing_year_debit_spend = ZEROIFNULL(MAX(spend))`, `customer_delivery_ts = MAX(load_ts)`; grouped by customer, name, town, branch_name, region_cd, segment | `legacy/views/v_customer_portfolio.sql:1-23` | same |

## 6. Reports (`reports/*.sql`)

| report | semantics | source | reconciles to |
|---|---|---|---|
| branch_league | `town, customer_count, net_amount, league_rank` from branch mart JOIN `dim_branch`, ordered by rank | `reports/branch_league.sql:2-4` | `gold.mart_branch_performance` + `dim_branch` (same as `06_branch_league.btq:48-51`) |
| card_services | `category, txn_count, spend, spend/NULLIFZERO(SUM(spend) OVER ())` ordered by spend desc | `reports/card_services.sql:2-3` | `gold.mart_card_spend_category` (same as `09_card_category.btq:47-49`) |
| finance_monthly | `SUM(closing_balance)`, `SUM(average_balance)` per month for `EXTRACT(YEAR FROM month_start)=2025` | `reports/finance_monthly.sql:2-4` | `gold.mart_monthly_balance`; year → parameter |
| risk_arrears | `customer_id, exposure, impaired_ratio` from `v_credit_risk` where `impaired_ratio > 0` (NULL ratio rows excluded) ordered by exposure desc | `reports/risk_arrears.sql:2-3` | `v_credit_risk` |

## 7. DDL defaults vs MERGE/INSERT column lists

Every target table carries audit/reference columns that no job writes; Teradata fills them from DDL `DEFAULT` on insert, and the goldens fill them from `data/column_defaults.json` (`golden/compute_baselines.py:107-111`). The translation must emit them as literals/parameters.

| table | columns set by DDL default | source | written explicitly by legacy |
|---|---|---|---|
| dim_customer | batch_id 20250630, load_ts, src_system_cd COREBANK, record_status_cd A, effective_date 2024-01-01, expiry_date 9999-12-31, customer_type_cd PERSONAL, residence_country_cd NO, preferred_language_cd nb, kyc_status_cd VERIFIED, consent_marketing_ind N | `legacy/ddl/nvb_edw.dim_customer.sql:12-22` | matched rows: batch_id, load_ts (`01_customer.btq:67`); inserted rows: none of these (`:68-71`) |
| fct_transaction | batch_id, load_ts, src_system_cd, record_status_cd, effective/expiry_date, currency_cd NOK, posting_status_cd POSTED, reversal_ind N, clearing_scheme_cd DOMESTIC | `legacy/ddl/nvb_edw.fct_transaction.sql:13-22` | none (`02_transactions.btq:58-62`) |
| dim_account | batch_id, load_ts, src_system_cd, record_status_cd, effective/expiry_date, currency_cd NOK, account_status_cd OPEN, accrual_basis_cd ACT365, overdraft_limit 0.00 | `legacy/ddl/nvb_edw.dim_account.sql:12-21` | `dormant_flag` only (`flag_dormant.sql:19-43`) — other columns pass through from input |
| etl_batch_control | see R-B3 | `legacy/ddl/nvb_edw.etl_batch_control.sql:11-21` | batch_date, job_name, status, row_count, last_step_cd |
| all six marts | batch_id, load_ts, src_system_cd, record_status_cd, effective/expiry_date | e.g. `legacy/ddl/nvb_mart.mart_monthly_balance.sql:11-16` | business columns only (macro/proc INSERT lists) |

[recomputed]: the default values in `data/column_defaults.json` equal the DDL defaults for every listed column, and the goldens carry them unchanged.

## 8. Legacy SQL vs `golden/compute_baselines.py` (parity risks)

Each row is a place where the SQL and the Python reference are written differently. "D0 rows affected" is the number of golden rows whose value would change if the translation followed the SQL form instead of the Python form (or vice versa), measured as above.

| # | area | legacy SQL | Python reference | D0 rows affected | guidance |
|---|---|---|---|---|---|
| P-1 | whitespace | `TRIM` (spaces) `01_customer.btq:29-33`, `02_transactions.btq:29-34` | `str.strip()` (all Unicode whitespace) `compute_baselines.py:25,27,32,41,43,44` | 0 (no padded values) | follow SQL (`trim`); add a test with tab/NBSP padding |
| P-2 | parse failure | `TRYCAST` → NULL → validation rejects delivery | `pd.to_numeric(errors="raise")` raises | 0 | both reject the delivery; translate as assertion |
| P-3 | amount scale | `TRYCAST(... AS DECIMAL(18,2))` | `to_numeric` float → `money(Decimal(str(value)))` HALF_UP `:42,50` | 0 (no >2 dp amounts) | `try_cast` to DECIMAL(18,2) (HALF_UP, X-2) |
| P-4 | dedup order | `ROW_NUMBER` with 4 DESC keys `01_customer.btq:52-56` | ascending sort, `drop_duplicates(keep="last")` `:35` | 0 (no ties, no NULLs) | follow SQL; full-key ties are non-deterministic in SQL and file-order-last in Python — add `NULLS LAST` and a deterministic final key if a tie test is needed |
| P-5 | dormancy window | `> ADD_MONTHS(as_of,-6) AND <= as_of`, active accounts of customers in active branches, active transactions `flag_dormant.sql:23-43` | `between("2024-12-31","2025-06-30")`, all accounts `:71-72` | 0 | follow SQL with `as_of_date` parameter |
| P-6 | segment stale cutoff | `< as_of_date - 90` `customer_segment.sql:31` | `< "2025-04-01"` `:91` | 0 (same date) | `date_sub(:as_of, 90)` |
| P-7 | segment window | `ADD_MONTHS(as_of,-12)+1 .. as_of`, active filters `:42-46` | `between("2024-07-01","2025-06-30")` `:87` | 0 | follow SQL |
| P-8 | branch window/filters | `txn_date BETWEEN 2024-01-01 AND as_of`, active filters `branch_league.sql:34-40` | all transactions, no filters `:93-95` | 0 (all txns in window, all active) | follow SQL |
| P-9 | branch NULL handling | `ZEROIFNULL(SUM)` and rank over the zeroed value | `.fillna(0)` then `rank(method="min")` `:95-96` | 0 (every branch has txns) | `rank()` = `method="min"` |
| P-10 | monthly average | `AVG(DECIMAL(28,6))` then CAST (18,2) `monthly_balance.sql:21` | integer cents sum / count, exact Decimal, HALF_UP `:15-19,81-82` | 0 with HALF_UP; 967 if HALF_EVEN; 1894 rows sit exactly on a half cent | must use HALF_UP cast (Spark default); never `bround` |
| P-11 | closing balance | `MAX(CASE WHEN date = month end ...)` `monthly_balance.sql:22-25` | last row by `balance_date` `:84` | 0 (completeness enforced) | follow SQL with `last_day` |
| P-12 | monthly filters | inner join active account, active balances, date window `:26-31` | no status/date filters; raises if unmatched `:79-80` | 0 | follow SQL |
| P-13 | product filters | active filters + 2024-01-01..2025-06-30 `07_product_profitability.btq:34-35` | none `:98` | 0 | follow SQL |
| P-14 | product precision | 6-dp intermediate (Teradata, [inference]) / 10-dp (Spark) | exact mean `:99-100` | 0 for both intermediates | `CAST(avg(...)*rate/100 AS DECIMAL(18,2))` |
| P-15 | channel normalisation | `COALESCE(NULLIF(TRIM(channel),''),'ONLINE')`, inner join calendar, active `channel_daily.sql:20-27` | groupby on already-cleaned channel `:102` | 0 | follow SQL |
| P-16 | card join | inner join active merchant, `COALESCE(category,'UNCLASSIFIED')`, window, active `card_category.sql:17-28` | `merge` (inner) on all merchant columns `:103-105` | 0 | follow SQL |
| P-17 | batch control | row maintained by jobs 01-10 + DDL defaults | single literal row + `column_defaults.json` `:106-111` | 0 | follow SQL; values from parameters |
| P-18 | numeric output type | DECIMAL(18,2) | Python float printed to CSV | 0 (all values printed with 2 dp) | keep DECIMAL(18,2); the comparator must compare decimals, not floats |

## 9. Edge cases D0 does not exercise (unit-test requirements)

Inferred from sections 0 and 3-4; each needs a hand-made fixture in the Wave 2/3 unit tests:

1. U+00A0 inside and at the ends of `customer_name`/`town` (R-C2).
2. Space/empty/lower-case/NULL `channel` (R-T3) and comma-decimal amounts (R-T2).
3. Two feed rows for one customer with equal `updated_at` (R-C5 tie keys).
4. Transaction exactly on `ADD_MONTHS(as_of,-6)` (excluded) and the day after (included) (R-D3/R-D4), and an `as_of_date` at a month end shorter than six months earlier (e.g. 2025-08-31 → 2025-02-28).
5. Customer with last transaction exactly `as_of - 90` (not DORMANT) and with no transactions in the window (NULL `last_txn_date`, spend 0, DORMANT) (R-S4/R-S5).
6. Two branches with equal `net_amount` (shared rank) (R-L3).
7. Half-cent monthly mean, positive and negative (R-M2, X-7).
8. Merchant row with NULL `category` (R-K1).

## 10. Proposed unit mapping

| contract unit | layer | target | rules | depends on |
|---|---|---|---|---|
| customer | silver | `silver.dim_customer` | R-C1..R-C7, X-1, X-3, X-4 | bronze landing, sql macros |
| transactions | silver | `silver.fct_transaction` | R-T1..R-T6, X-2 | bronze landing |
| dormant | silver | `silver.dim_account` | R-D1..R-D5, X-13 | customer, transactions |
| monthly_balance | gold | `gold.mart_monthly_balance` | R-M1..R-M5, X-7, X-8, X-16, X-17 | bronze `fct_daily_balance`, dormant (`dim_account`) |
| customer_segment | gold | `gold.mart_customer_segment` | R-S1..R-S7, X-14 | customer, dormant, transactions |
| branch_league | gold | `gold.mart_branch_performance` | R-L1..R-L4, X-19 | customer, dormant, transactions |
| product_profitability | gold | `gold.mart_product_profitability` | R-P1..R-P4, X-8, X-9 | dormant, bronze `fct_daily_balance`, `dim_product` |
| channel | gold | `gold.mart_channel_daily` | R-H1..R-H4 | transactions, `dim_calendar` |
| card_category | gold | `gold.mart_card_spend_category` | R-K1..R-K3 | transactions, `dim_merchant` |
| close_batch | silver | `silver.etl_batch_control` | R-B1..R-B3, section 2 | channel and all prior units |

Business views (section 5) and reports (section 6) belong to Wave 3 alongside their marts.
