# DAIWT-01 target data model and rationalization (Wave 0 discovery)

Scope: the Nordvik Sparebank Teradata 17 estate (`nvb_stg`, `nvb_edw`, `nvb_mart`) in `daiwt-01-teradata-warehouse`
(legacy repo, read-only, commit `cf4b4fa` per golden `manifest.json` `source.legacy_commit`) mapped onto the medallion
layout of `daiwt-01-lakehouse-migration`. Pointers are `path:line` in the legacy repo unless prefixed `mig:` (migration
repo) or `golden:` (`/Volumes/daiwt_01_mig_prod/landing/golden/`).

Evidence labels used below:
- **[verified]** recomputed or checked against the D0 golden files (DuckDB over `golden:inputs/D0/*/*.csv`, compared
  row-for-row with `golden:outputs/D0/<unit>/<table>.csv`), or probed read-only on warehouse `6dc4b80538621b7b`.
- **[source]** read directly from the cited file.
- **[inference]** design proposal or reasoning not proven by data.

## 1. Catalogs, schemas, volumes

| Environment | Catalog | Schemas | Source |
| --- | --- | --- | --- |
| prod | `daiwt_01_mig_prod` | `bronze`, `silver`, `gold` (fixed; owned by Terraform, not the bundle) | `mig:databricks.yml:56-61`, `mig:README.md` "Targets and authentication" |
| dev (per PR) | `daiwt_01_mig_dev` | `bronze_pr_<n>`, `silver_pr_<n>`, `gold_pr_<n>` (`<layer>_${var.schema_suffix}`) | `mig:databricks.yml:9-14,31-44` |
| landing (prod only) | `daiwt_01_mig_prod.landing` | volumes `golden` (read-only baseline) and `bronze_landing` | brief / UC |

Legacy database -> medallion layer (names never carry `nvb_*`):

| Teradata database | Role (`README.md:10-13`) | Target layer |
| --- | --- | --- |
| `nvb_stg` | landing deliveries (`customer_feed`, `transaction_feed`) | `bronze` |
| `nvb_edw` | conformed dimensions, ledger facts, batch control | `silver` |
| `nvb_mart` | consumer summaries (6 tables) and business views (3) | `gold` |
| upstream FastExport deliveries that load `nvb_edw` directly (9 tables, `README.md:19-21`) | raw landing | `bronze` (as delivered) -> `silver` (typed) |

### 1.1 Parity contract mechanics (drives every type decision)

- `mig:src/parity/contract.json` has 10 outputs, all drop `D0`, units fixed: `customer`, `transactions`, `dormant`,
  `monthly_balance`, `customer_segment`, `branch_league`, `product_profitability`, `channel`, `card_category`,
  `close_batch`. There is no `bronze.*` target and no view/report target.
- [verified] For all 10 outputs: CSV header == contract column list (same order), data rows == `row_count`, contract
  `keys` are unique, and no cell is `\N` (no NULLs in any D0 golden).
- Golden rendering (`golden:manifest.json` `csv_conventions.outputs`): comma CSV, RFC4180 minimal quoting, `\N` = NULL,
  integers as digits, decimals at the fixed DDL scale with ROUND_HALF_UP, dates `YYYY-MM-DD`, `TIMESTAMP(0)` as
  `YYYY-MM-DDTHH:MM:SS.000Z`, CHAR "as delivered (no Teradata CHAR padding)", column order = legacy DDL order minus
  excluded columns.
- `load_ts` is dropped from every golden (`manifest.json` `excluded_from_parity`, constant `2025-06-30 23:00:00`), so
  every golden has the DDL column list minus `load_ts`.
- [verified] Databricks rendering probe (warehouse `6dc4b80538621b7b`, session TZ `Etc/UTC`):

| Databricks expression | Rendered | Golden need | Consequence |
| --- | --- | --- | --- |
| `CAST(0 AS DECIMAL(18,2))` | `0.00` | `overdraft_limit` = `0.00` (input file has `0`) | declare DDL scale; never leave as INT/DOUBLE |
| `CAST(1.5 AS DECIMAL(6,2))` | `1.50` | `annual_rate` = `1.50` | keep `DECIMAL(6,2)` |
| `TIMESTAMP '2025-06-30 20:00:00'` (LTZ) | `2025-06-30T20:00:00.000Z` | `updated_at` = `2025-06-30T20:00:00.000Z` | use `TIMESTAMP` (LTZ), session UTC |
| `TIMESTAMP_NTZ` same value | `2025-06-30T20:00:00.000` | - | **NTZ fails parity** (no `Z`) |
| `CAST('A' AS CHAR(1))` | `A` (STRING) | `A` | no padding needed; use STRING |
| `CAST(2.345 AS DECIMAL(18,2))`, `2.355`, `-2.345` | `2.35`, `2.36`, `-2.35` | ROUND_HALF_UP (away from zero) | Databricks CAST/ROUND == golden rounding |
| `DATE '9999-12-31'`, `BIGINT 20250630` | `9999-12-31`, `20250630` | same | match |

## 2. Type map (Teradata -> Delta)

| Teradata (`legacy/ddl/*`) | Delta | Rule |
| --- | --- | --- |
| `INTEGER` | `INT` | `COUNT(*)` returns `BIGINT` in Databricks; cast to `INT` where the golden type is `INTEGER` (`txn_count`, `customer_count`, `league_rank`) so the table schema matches (render is identical) |
| `BIGINT` | `BIGINT` | `transaction_id`, `batch_id`, `row_count`, `reject_count` |
| `DECIMAL(p,s)` | `DECIMAL(p,s)` | money `DECIMAL(18,2)`; rates `DECIMAL(6,2)`; intermediates `DECIMAL(28,6)` as in `legacy/macros/monthly_balance.sql:21`, `legacy/bteq/07_product_profitability.btq:29` |
| `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | |
| `TIMESTAMP(0)` | `TIMESTAMP` (LTZ) | no fractional seconds in any input; UTC session; see 1.1 |
| `VARCHAR(n)` / `CHAR(n)` `CHARACTER SET LATIN/UNICODE` | `STRING` | [verified] longest values: customer_name 19, town 7, channel 6; no CHAR padding in goldens |
| `NOT CASESPECIFIC` | `STRING` (binary) | Teradata compares case-insensitively; [verified] no case variants in D0 (all channels upper, names unique per id). Optional `COLLATE UTF8_LCASE` is not needed for parity |
| `COMPRESS (...)` | dropped | Delta/Parquet encoding |
| `CHECK (...)` | Delta `CHECK` constraint or Lakeflow `expect_or_fail` | `record_status_cd IN ('A','D')`, `overdraft_limit >= 0`, `minimum_balance >= 0`, `contractual_rate >= 0`, `reject_count >= 0`, `consent_marketing_ind IN ('N','Y')` |
| `DEFAULT <literal>` | explicit literal in SELECT (or drop parameter) | defaults are the only source of the standard columns (2.1) |
| `SET` table | no equivalent | SET silently drops whole-row duplicates; [verified] 0 full-row duplicates in every D0 input |
| `UNIQUE PRIMARY INDEX` / `PRIMARY INDEX` / `NO PRIMARY INDEX` | dropped; optional `CLUSTER BY` | key uniqueness enforced by validation, not storage |
| `PARTITION BY RANGE_N(... EACH INTERVAL '1' MONTH)` | optional `CLUSTER BY (date col)` | `fct_transaction.sql:25`, `fct_daily_balance.sql:22`; no output impact |
| `COLLECT STATISTICS` | dropped | Databricks predictive optimization / ANALYZE |

### 2.1 Standard audit columns (every `nvb_edw` / `nvb_mart` table)

`batch_id BIGINT`, `load_ts TIMESTAMP(0)`, `src_system_cd VARCHAR(12)`, `record_status_cd CHAR(1)`,
`effective_date DATE`, `expiry_date DATE` immediately follow the business columns (e.g. `nvb_edw.dim_account.sql:12-17`,
`nvb_mart.mart_channel_daily.sql:11-16`). Values come only from DDL defaults / `data/column_defaults.json`
(`20250630`, `2025-06-30 23:00:00`, `COREBANK`, `A`, `2024-01-01`, `9999-12-31`); `golden/compute_baselines.py:182-186`
appends them to every output. Semantics to preserve:

- `effective_date`/`expiry_date` are **not** SCD2 tracking: every row in every table carries `2024-01-01`/`9999-12-31`
  [verified in all 10 goldens and all edw inputs]; no legacy object ever writes them. Silver/gold carry them as constants.
- `record_status_cd` is always `A` [verified]; legacy SQL filters on it (`legacy/procs/*.sql`, `legacy/macros/*.sql`,
  `legacy/views/*.sql`). Keep the `= 'A'` predicates for fidelity; they remove 0 rows at D0.
- `batch_id` and the business date are literals in legacy code (`20250630`, `DATE '2025-06-30'`; `README.md:28-31`).
  [inference] In the target they come from the drop (`golden:manifest.json` `drops[0]` = `{D0, 20250630, 2025-06-30}`;
  `mig:ci/drops.py`), one parameter feeding every unit.
- `load_ts` is materialised as the constant `TIMESTAMP '2025-06-30 23:00:00'` (UTC) for lineage; it is excluded from parity.

## 3. Bronze layer (raw landing; 11 feeds; no golden)

Source: `golden:inputs/D0/<db.table>/<db.table>.csv` (pipe-delimited, header, UTF-8, LF; byte-identical to the S3 landing
delivery per `manifest.json` `md5_matches_s3_manifest`) with a `.parquet` alternate encoding of the same delivery
(`README.md:22`). Bronze keeps every column as `STRING` exactly as delivered (the `nvb_stg` DDL itself is all-VARCHAR,
`nvb_stg.customer_feed.sql:7-11`, `nvb_stg.transaction_feed.sql:7-12`), plus optional lineage columns (`_source_file`,
`_drop`) that silver drops. Read with `read_files(path, format=>'csv', sep=>'|', header=>true, schema=>'<all STRING>')`;
there is no `\N` and no empty field in any input [verified], so null handling is moot at D0. Load mode: full replace per
drop (`README.md:19-21` "replaced, not appended"; `legacy/schedules/nightly.yaml:17`).

Bronze table names drop the legacy database prefix and keep the delivery's table name for one-to-one traceability.

| Bronze table | Delivery folder | Columns (all STRING, file order) | Rows [verified] | Grain / key [verified unique] |
| --- | --- | --- | --- | --- |
| `bronze.customer_feed` | `nvb_stg.customer_feed` | customer_id, customer_name, branch_id, town, updated_at | 5100 | one row per customer *version*; customer_id is **not** unique (100 ids twice: an older `2024-01-01 00:00:00` row with town `Voss` plus the current `2025-06-30 20:00:00` row; `data/generate.py:22-25`, `README.md:24`); no ties on updated_at |
| `bronze.transaction_feed` | `nvb_stg.transaction_feed` | transaction_id, account_id, txn_date, amount, channel, merchant_id | 180000 | transaction_id unique |
| `bronze.dim_account` | `nvb_edw.dim_account` | account_id, customer_id, product_id, opened_date, dormant_flag + 2.1 cols + currency_cd, account_status_cd, accrual_basis_cd, overdraft_limit | 8000 | account_id |
| `bronze.dim_branch` | `nvb_edw.dim_branch` | branch_id, town + 2.1 + branch_name, region_cd, branch_type_cd, service_language_cd, branch_status_cd | 8 | branch_id |
| `bronze.dim_calendar` | `nvb_edw.dim_calendar` | calendar_date, month_start, year_no, month_no + 2.1 + calendar_name, business_day_rule_cd | 547 | calendar_date (2024-01-01..2025-06-30) |
| `bronze.dim_card` | `nvb_edw.dim_card` | card_id, account_id, card_type + 2.1 + network_cd, card_status_cd, card_expiry_date, contactless_ind, online_enabled_ind | 6000 | card_id (account_id also unique, 1..6000) |
| `bronze.dim_merchant` | `nvb_edw.dim_merchant` | merchant_id, merchant_name, category + 2.1 + country_cd, settlement_currency_cd, acquirer_cd, merchant_status_cd | 80 | merchant_id |
| `bronze.dim_product` | `nvb_edw.dim_product` | product_id, product_name, annual_rate + 2.1 + product_family_cd, currency_cd, product_status_cd, rate_basis_cd, minimum_balance | 4 | product_id |
| `bronze.fct_daily_balance` | `nvb_edw.fct_daily_balance` | account_id, balance_date, balance + 2.1 + balance_type_cd, currency_cd, accrued_interest, reserved_amount | 4376000 | (account_id, balance_date); 8000 accounts x 547 days, complete |
| `bronze.fct_loan` | `nvb_edw.fct_loan` | loan_id, account_id, outstanding, arrears_days + 2.1 + loan_status_cd, currency_cd, contractual_rate, repayment_frequency_cd, collateral_type_cd | 1800 | loan_id (account_id also unique, 1..1800) |
| `bronze.fct_product_order` | `nvb_edw.fct_product_order` | order_id, customer_id, product_id, order_date, status + 2.1 + application_channel_cd, decision_system_cd, currency_cd, manual_review_ind | 3500 | order_id |

Input text forms that silver must parse [verified]: decimals in edw feeds are written by pandas `float_format="%.2f"`
except zero-valued defaults, which are written as `0` (`overdraft_limit`, `minimum_balance`, `accrued_interest`,
`reserved_amount`); `annual_rate` as `1.50`; staging `amount` always `%.2f` (`data/generate.py:41-42,59`). Timestamps
`YYYY-MM-DD HH:MM:SS`. No leading/trailing blanks, no NBSP, no comma decimals, no lowercase or empty channel in D0,
so the cleansing rules in 01/02 BTEQ are no-ops on D0 but must still be implemented (section 4 notes).

Owning unit: `bronze_landing` (Wave 1). Bronze has no golden; its check is file row count == `inputs/D0/_manifest.json`
`rows` and the delivery md5/sha256 in `manifest.json` `files`.

## 4. Silver layer (typed `nvb_edw`)

Silver column lists = legacy DDL order, types per section 2, including `load_ts` (stored, not compared). Contract
silver tables must expose exactly the golden columns in golden order (contract order = DDL order minus `load_ts`).

### 4.1 Contract silver tables (golden-checked)

| Silver table | Legacy DDL | Grain / key [verified unique in golden] | Columns -> Delta type (golden order; `load_ts TIMESTAMP` stored after `batch_id`, excluded) | Temporal handling (legacy semantics) | Source feed(s) | Producing legacy object | Golden (`contract.json` path) | Unit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `silver.dim_customer` | `nvb_edw.dim_customer.sql:7-24` (UPI customer_id `:24`) | 1 row per customer; `customer_id` (5000) | customer_id INT, customer_name STRING, branch_id INT, town STRING, updated_at TIMESTAMP, batch_id BIGINT, src_system_cd STRING, record_status_cd STRING, effective_date DATE, expiry_date DATE, customer_type_cd STRING, residence_country_cd STRING, preferred_language_cd STRING, kyc_status_cd STRING, consent_marketing_ind STRING | Type-1 current-state **MERGE** upsert (`01_customer.btq:62-71`): latest version per id by `ORDER BY updated_at DESC, customer_name DESC, town DESC, branch_id DESC` (`:52-56`); matched rows overwritten, unmatched inserted with DDL defaults; customers absent from a delivery are retained (no delete). Cleansing: `TRYCAST(TRIM(...))` ids/timestamp, NBSP -> space then TRIM on name/town (`:29-34`); whole delivery rejected if any id null/<=0, empty name, null updated_at or unknown branch (`:39-46`) | `bronze.customer_feed`, `silver.dim_branch` (FK check) | `legacy/bteq/01_customer.btq` | `outputs/D0/customer/dim_customer.csv` (5000 rows) | `customer` |
| `silver.fct_transaction` | `nvb_edw.fct_transaction.sql:7-25` (PI transaction_id, monthly RANGE_N) | 1 row per payment; `transaction_id` (180000) | transaction_id BIGINT, account_id INT, txn_date DATE, amount DECIMAL(18,2), channel STRING, merchant_id INT, batch_id BIGINT, src_system_cd, record_status_cd STRING, effective_date, expiry_date DATE, currency_cd, posting_status_cd, reversal_ind, clearing_scheme_cd STRING | **Window replace** in one BT/ET transaction: delete `txn_date BETWEEN 2024-01-01 AND 2025-06-30`, insert the cleansed delivery (`02_transactions.btq:53-64`) == full overwrite of the retained window. Cleansing (`:29-34`): TRYCAST/TRIM ids and date, `,`->`.` in amount, `COALESCE(NULLIF(UPPER(TRIM(channel)),''),'ONLINE')`. Whole delivery rejected on null/<=0 id, null amount/date, date outside window, unknown account/merchant, channel not in CARD/MOBILE/BRANCH/ONLINE (`:38-47`) or duplicate id (`:48-50`) | `bronze.transaction_feed`, `silver.dim_account` (feed copy), `silver.dim_merchant` | `legacy/bteq/02_transactions.btq` | `outputs/D0/transactions/fct_transaction.csv` (180000) | `transactions` |
| `silver.dim_account` | `nvb_edw.dim_account.sql:7-23` (UPI account_id) | 1 row per account; `account_id` (8000) | account_id INT, customer_id INT, product_id INT, opened_date DATE, dormant_flag STRING, batch_id BIGINT, src_system_cd, record_status_cd STRING, effective_date, expiry_date DATE, currency_cd, account_status_cd, accrual_basis_cd STRING, overdraft_limit DECIMAL(18,2) | Upstream replacement delivery (`README.md:21,23`) followed by in-place **UPDATE** of `dormant_flag`: reset all active to `N`, then per active branch set `Y` where the account has no payment with `txn_date > ADD_MONTHS(as_of,-6) AND <= as_of` (`legacy/procs/flag_dormant.sql:19-46`); control re-derives `Y` iff last payment is NULL or `<= ADD_MONTHS(2025-06-30,-6)` (`03_dormant.btq:42-50`). Target: one derived select (feed + computed flag), no UPDATE | `bronze.dim_account`, `silver.fct_transaction`, `silver.dim_customer` | `legacy/bteq/03_dormant.btq`, `legacy/procs/flag_dormant.sql` | `outputs/D0/dormant/dim_account.csv` (8000) | `dormant` |
| `silver.etl_batch_control` | `nvb_edw.etl_batch_control.sql:7-23` (PI batch_date) | 1 row per (batch_date, job_name); D0: 1 row | batch_date DATE, job_name STRING, status STRING, row_count BIGINT, batch_id BIGINT, src_system_cd, record_status_cd STRING, effective_date, expiry_date DATE, last_step_cd STRING, window_start_date, window_end_date DATE, reject_count BIGINT, scheduler_group_cd STRING | Single delivery-level **state row** (not job history, `legacy/schedules/runbook.md:27-32`): each job registers it (`OPEN`), sets `RUNNING`/`last_step_cd`; any failure sets `FAILED`; only job 10 sets `COMPLETE` with `row_count = COUNT(*) fct_transaction` after reconciling against `mart_channel_daily` (`10_close_batch.btq:26-48`). Golden = final state: `2025-06-30, NIGHTLY, COMPLETE, 180000, ..., 10_close_batch, 2024-01-01, 2025-06-30, 0, NVB_NIGHTLY` | `silver.fct_transaction`, `gold.mart_channel_daily` | `legacy/bteq/01..10_*.btq` header/footer blocks, `10_close_batch.btq` | `outputs/D0/close_batch/etl_batch_control.csv` (1) | `close_batch` |

[verified] Re-implementing the legacy SQL semantics above (DuckDB) over `inputs/D0` reproduces the business columns of
`dim_customer`, `fct_transaction` and `dim_account` with 0 mismatching rows; `dormant_flag = 'Y'` for 105 accounts (100
accounts 7901-8000 have no payments at all, `data/generate.py:31`; 5 have their last payment on or before 2024-12-30).

Date-function semantics to preserve [source + verified]: Teradata `ADD_MONTHS(DATE '2025-06-30', -6)` = `2024-12-30`
(day number kept, no month-end snap); the runbook states the inclusive first recent date is 2024-12-31
(`runbook.md:48-50`). Databricks `add_months(DATE'2025-06-30', -6)` also returns `2024-12-30` (Spark 3 semantics);
do not replace with `- INTERVAL 180 DAYS` (`runbook.md:48`).

### 4.2 Reference dimensions and upstream facts (no golden; checked against legacy control metrics)

These are delivered upstream and replaced before job 01 (`README.md:20-21`, `nightly.yaml:12-17`); no legacy job
transforms them. Silver = typed copy of bronze (cast per section 2, `0` -> `0.00` at DECIMAL scale). Grain/keys
[verified unique on D0]. Check = metrics in `golden:controls/D0/expected/nvb_edw.<table>.json` (rows, distinct `_id`
counts, numeric sums, date min/max, code value counts; produced by `golden/compute_baselines.py:190-203`).

| Silver table | Legacy DDL | Grain / key | Columns -> Delta type | Temporal handling | Source | Consumers | Golden | Proposed unit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `silver.dim_branch` | `nvb_edw.dim_branch.sql:7-21` | branch_id (8) | branch_id INT, town STRING, 2.1 cols, branch_name, region_cd, branch_type_cd, service_language_cd, branch_status_cd STRING | current-state overwrite per delivery | `bronze.dim_branch` | 01 FK check, 06, `flag_dormant` cursor, views, reports | none (controls `nvb_edw.dim_branch.json`) | `reference` |
| `silver.dim_product` | `nvb_edw.dim_product.sql:7-22` | product_id (4) | product_id INT, product_name STRING, annual_rate DECIMAL(6,2), 2.1, product_family_cd, currency_cd, product_status_cd, rate_basis_cd STRING, minimum_balance DECIMAL(18,2) | overwrite | `bronze.dim_product` | 07 | none (controls) | `reference` |
| `silver.dim_merchant` | `nvb_edw.dim_merchant.sql:7-21` | merchant_id (80) | merchant_id INT, merchant_name STRING, category STRING, 2.1, country_cd, settlement_currency_cd, acquirer_cd, merchant_status_cd STRING | overwrite | `bronze.dim_merchant` | 02 FK check, 09 | none (controls) | `reference` |
| `silver.dim_calendar` | `nvb_edw.dim_calendar.sql:7-20` | calendar_date (547) | calendar_date DATE, month_start DATE, year_no INT, month_no INT, 2.1, calendar_name, business_day_rule_cd STRING | overwrite | `bronze.dim_calendar` | 08 check + `channel_daily` inner join, `v_channel_trend` | none (controls) | `reference` |
| `silver.dim_card` | `nvb_edw.dim_card.sql:7-22` | card_id (6000) | card_id INT, account_id INT, card_type STRING, 2.1, network_cd, card_status_cd STRING, card_expiry_date DATE, contactless_ind, online_enabled_ind STRING | overwrite | `bronze.dim_card` | none in legacy SQL (no reference in bteq/procs/macros/views/reports) | none (controls) | `reference` |
| `silver.fct_daily_balance` | `nvb_edw.fct_daily_balance.sql:7-22` | (account_id, balance_date) (4,376,000) | account_id INT, balance_date DATE, balance DECIMAL(18,2), 2.1, balance_type_cd, currency_cd STRING, accrued_interest, reserved_amount DECIMAL(18,2) | full replace per delivery; completeness control: every account-month has every day through calendar month-end (`04_monthly_balance.btq:26-39`, `runbook.md:51-52`) | `bronze.fct_daily_balance` | 04, 07 | none (controls) | `reference` |
| `silver.fct_loan` | `nvb_edw.fct_loan.sql:7-23` | loan_id (1800) | loan_id INT, account_id INT, outstanding DECIMAL(18,2), arrears_days INT, 2.1, loan_status_cd, currency_cd STRING, contractual_rate DECIMAL(6,2), repayment_frequency_cd, collateral_type_cd STRING | full replace | `bronze.fct_loan` | `v_credit_risk` -> `reports/risk_arrears.sql` | none (controls) | `reference` |
| `silver.fct_product_order` | `nvb_edw.fct_product_order.sql:7-23` | order_id (3500) | order_id INT, customer_id INT, product_id INT, order_date DATE, status STRING, 2.1, application_channel_cd, decision_system_cd, currency_cd, manual_review_ind STRING | full replace | `bronze.fct_product_order` | none in legacy SQL | none (controls) | `reference` |

[inference] One `reference` silver unit (Wave 2, or folded into the Wave 1 silver pipeline scaffold) owns all eight;
contract unit names are fixed and none of the ten covers them. Referential integrity [verified on D0]: every payment
account and merchant exists; every account's customer is in the customer feed; every loan account exists; every order
customer exists; 0 orphans.

## 5. Gold layer (`nvb_mart`)

All six marts are full rebuilds of their slice (`runbook.md:14-15` "All summary builds replace their content"):
delete-by-`batch_id = 20250630` + insert (procs 05/06, 07, macro `card_category`) or delete-by-date-range + insert
(macros `monthly_balance`, `channel_daily`). [inference] Target: `CREATE OR REPLACE TABLE ... AS` / Lakeflow
materialized view per mart for D0; if multiple drops are retained later, `INSERT ... REPLACE WHERE batch_id = :batch_id`
preserves the slice semantics.

| Gold table | Legacy DDL | Grain / key [verified unique] | Columns -> Delta type (golden order) | Logic to preserve | Source | Producing legacy object | Golden | Unit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gold.mart_monthly_balance` | `nvb_mart.mart_monthly_balance.sql:7-18` | (account_id, month_start) 144000 = 8000 x 18 | account_id INT, month_start DATE, average_balance DECIMAL(18,2), closing_balance DECIMAL(18,2), + 2.1 (batch_id BIGINT, src_system_cd STRING, record_status_cd STRING, effective_date DATE, expiry_date DATE) | `TRUNC(balance_date,'MONTH')`; `CAST(AVG(CAST(balance AS DECIMAL(28,6))) AS DECIMAL(18,2))`; closing = balance on calendar month-end (`legacy/macros/monthly_balance.sql:18-32`); active rows only; published only if every account-month is complete (`04_monthly_balance.btq:34-39`) | `silver.fct_daily_balance`, `silver.dim_account` | `04_monthly_balance.btq`, `legacy/macros/monthly_balance.sql` | `outputs/D0/monthly_balance/mart_monthly_balance.csv` | `monthly_balance` |
| `gold.mart_customer_segment` | `nvb_mart.mart_customer_segment.sql:7-19` | customer_id 5000 | customer_id INT, txn_count INT, spend DECIMAL(18,2), last_txn_date DATE, segment STRING, + 2.1 | window `ADD_MONTHS(as_of,-12)+1 .. as_of` = 2024-07-01..2025-06-30; spend = sum of `-amount` for negative amounts; DORMANT if last payment NULL or `< as_of - 90` (2025-04-01), PREMIUM if >=50 payments and spend >= 20000, ACTIVE if >=20, else OCCASIONAL; all active customers via LEFT JOINs (`legacy/procs/customer_segment.sql:25-47`); ZEROIFNULL spend | `silver.dim_customer`, `silver.dim_account`, `silver.fct_transaction` | `05_customer_segment.btq`, `legacy/procs/customer_segment.sql` | `outputs/D0/customer_segment/mart_customer_segment.csv` | `customer_segment` |
| `gold.mart_branch_performance` | `nvb_mart.mart_branch_performance.sql:7-18` | branch_id 8 | branch_id INT, customer_count INT, net_amount DECIMAL(18,2), league_rank INT, + 2.1 | customers grouped by `dim_customer.branch_id`; `COUNT(DISTINCT customer_id)`; `ZEROIFNULL(SUM(amount))` over 2024-01-01..as_of; `RANK() OVER (ORDER BY net DESC)` (`legacy/procs/branch_league.sql:28-41`); row count must equal distinct active branches (`:44-46`) | `silver.dim_customer`, `silver.dim_account`, `silver.fct_transaction` | `06_branch_league.btq`, `legacy/procs/branch_league.sql` | `outputs/D0/branch_league/mart_branch_performance.csv` | `branch_league` |
| `gold.mart_product_profitability` | `nvb_mart.mart_product_profitability.sql:7-17` | product_id 4 | product_id INT, average_balance DECIMAL(18,2), annual_income DECIMAL(18,2), + 2.1 | per product: `AVG(CAST(balance AS DECIMAL(28,6)))` over all account-days 2024-01-01..2025-06-30, `MAX(annual_rate)`; average_balance = CAST(.. AS DECIMAL(18,2)); annual_income = CAST(precise_balance * annual_rate / 100 AS DECIMAL(18,2)) (`07_product_profitability.btq:26-51`) | `silver.fct_daily_balance`, `silver.dim_account`, `silver.dim_product` | `07_product_profitability.btq` | `outputs/D0/product_profitability/mart_product_profitability.csv` | `product_profitability` |
| `gold.mart_channel_daily` | `nvb_mart.mart_channel_daily.sql:7-18` | (txn_date, channel) 2188 = 547 days x 4 channels | txn_date DATE, channel STRING, txn_count INT, net_amount DECIMAL(18,2), + 2.1 | `COALESCE(NULLIF(TRIM(channel),''),'ONLINE')`, `COUNT(*)`, `ZEROIFNULL(SUM(amount))`, inner join `dim_calendar` (`legacy/macros/channel_daily.sql:18-28`); reconciled to `fct_transaction` per day/channel (`08_channel.btq:40-45`) | `silver.fct_transaction`, `silver.dim_calendar` | `08_channel.btq`, `legacy/macros/channel_daily.sql` | `outputs/D0/channel/mart_channel_daily.csv` | `channel` |
| `gold.mart_card_spend_category` | `nvb_mart.mart_card_spend_category.sql:7-17` | category 4 (FUEL, GROCERY, RETAIL, TRAVEL) | category STRING, txn_count INT, spend DECIMAL(18,2), + 2.1 | `channel = 'CARD' AND amount < 0`, inner join merchant, `COALESCE(category,'UNCLASSIFIED')`, `SUM(-amount)` (`legacy/macros/card_category.sql:17-29`) | `silver.fct_transaction`, `silver.dim_merchant` | `09_card_category.btq`, `legacy/macros/card_category.sql` | `outputs/D0/card_category/mart_card_spend_category.csv` | `card_category` |

[verified] Re-implementing each mart's legacy SQL (DuckDB, HALF_UP casts) over `inputs/D0` reproduces the business
columns of all six gold goldens with 0 mismatching rows. Rounding note [verified]: 1,894 of 144,000 account-months
have an exact monthly mean that lies exactly on a half cent, so `average_balance` passes parity only with
ROUND_HALF_UP (Databricks `CAST`/`ROUND` default, section 1.1); `bround`/half-even rounding would fail those rows.
Compute averages from `DECIMAL(28,6)` (or exact cents) and cast once; do not average `DOUBLE`.

## 6. Business views and reports (no golden)

### 6.1 Publication in gold

| Target | Legacy | Grain | Teradata-specific constructs -> Databricks | Expected D0 shape [verified by recompute from inputs] | Unit |
| --- | --- | --- | --- | --- | --- |
| `gold.v_customer_portfolio` (UC view) | `legacy/views/v_customer_portfolio.sql:1-22` | customer (active), joined to branch, accounts, segment | `ZEROIFNULL(MAX(s.spend))` -> `coalesce(max(spend),0)`; `MAX(c.load_ts)` passes through | 5000 rows | `business_views` |
| `gold.v_credit_risk` (UC view) | `legacy/views/v_credit_risk.sql:1-23` | (customer_id, customer_name, branch_town) | `NULLIFZERO` -> `nullif(x,0)`; ratio scale must be set explicitly (Teradata vs Databricks decimal division scale differ) | 1800 rows (1 loan per account 1..1800, 1 account per customer in that range); bands IMPAIRED 329 / WATCH 314 / PERFORMING 1157 | `business_views` |
| `gold.v_channel_trend` (UC view) | `legacy/views/v_channel_trend.sql:1-21` | calendar day (MOBILE channel only, `:7,20`) | `CSUM(x, d)` -> `sum(x) over (order by d rows unbounded preceding)`; `MAVG(x,7,d)` -> `avg(x) over (order by d rows between 6 preceding and current row)`; `ZEROIFNULL`/`NULLIFZERO` | 547 rows | `business_views` |
| `gold.report_branch_league` (view) | `reports/branch_league.sql:2-4` | branch, ordered by league_rank | `SEL` -> `SELECT` | 8 rows | `reports` |
| `gold.report_card_services` (view) | `reports/card_services.sql:2-3` | category | `NULLIFZERO`, `SUM() OVER ()` | 4 rows | `reports` |
| `gold.report_finance_monthly` (view) | `reports/finance_monthly.sql:2-4` | month_start in 2025 | `EXTRACT(YEAR FROM ...)` unchanged | 6 rows | `reports` |
| `gold.report_risk_arrears` (view) | `reports/risk_arrears.sql:2-3` | customer with impaired_ratio > 0 | - | 329 rows | `reports` |

The BTEQ "display" SELECTs at the end of each job (e.g. `08_channel.btq:48-50` average payment, `09_card_category.btq:47-49`
share of debit spend, `04_monthly_balance.btq:50-51`) and the export files (`02_transactions.btq:68-71`,
`10_close_batch.btq:51-53`) are operator control output, not published objects (`golden:skipped_branches.json`).
[inference] Publish them as task-level control queries in the job (or a `gold.control_*` view) rather than tables.
`ORDER BY` in report SQL is presentation only; views keep it out (ordering is not part of multiset parity).

### 6.2 Reconciliation without a golden [inference: proposal]

1. **Derived-golden check (reports):** every report is a pure function of a golden-checked mart (+ `dim_branch`).
   Evaluate the report SQL over the golden mart CSVs (loaded into a scratch dev table) and compare multiset-equal with
   the published view in the same PR; any diff is in the view logic, since the inputs are proven equal.
2. **Control-metric parity (reference silver, views' inputs):** compare `rows`, `distinct.*_id`, `sums.*`,
   `dates.*.min/max` and `*_counts` from `golden:controls/D0/expected/nvb_edw.<table>.json` with the same metrics
   computed on the silver table (definition `golden/compute_baselines.py:190-203`); covers the eight silver tables in
   4.2 and the bronze feeds (`nvb_stg.*.json`).
3. **Legacy control invariants as tests:** port the BTEQ post-build checks as SQL assertions on the views/marts, e.g.
   `SUM(txn_count)`/`SUM(net_amount)` over `mart_channel_daily` == `COUNT(*)`/`SUM(amount)` over `fct_transaction`
   (`10_close_batch.btq:32-40`), report row counts above, `v_credit_risk` bands sum to the loan-customer count.
4. **Independent recompute for views:** views over loans/accounts (`v_credit_risk`, `v_customer_portfolio`) are checked
   against a deterministic recompute from `inputs/D0` (as done for the counts in 6.1), stored as a small expected CSV
   produced by the unit's builder and reviewed in the PR.

## 7. Unit / ownership summary

| Unit | Wave | Owns | Golden outputs | Depends on |
| --- | --- | --- | --- | --- |
| `bronze_landing` | 1 | 11 `bronze.*` tables (section 3) | none (manifest row counts / checksums) | - |
| `reference` | 1-2 | `silver.dim_branch, dim_product, dim_merchant, dim_calendar, dim_card, fct_daily_balance, fct_loan, fct_product_order` | none (controls metrics) | `bronze_landing` |
| `customer` | 2 | `silver.dim_customer` | `silver.dim_customer` | `bronze_landing`, `reference` |
| `transactions` | 2 | `silver.fct_transaction` | `silver.fct_transaction` | `bronze_landing`, `reference` |
| `dormant` | 2 | `silver.dim_account` | `silver.dim_account` | `bronze_landing`, `customer`, `transactions` |
| `monthly_balance` | 3 | `gold.mart_monthly_balance` | same | `reference`, `dormant` |
| `customer_segment` | 3 | `gold.mart_customer_segment` | same | `customer`, `dormant`, `transactions` |
| `branch_league` | 3 | `gold.mart_branch_performance` | same | `customer`, `dormant`, `transactions` |
| `product_profitability` | 3 | `gold.mart_product_profitability` | same | `reference`, `dormant` |
| `channel` | 3 | `gold.mart_channel_daily` | same | `transactions`, `reference` |
| `card_category` | 3 | `gold.mart_card_spend_category` | same | `transactions`, `reference` |
| `close_batch` | 3 | `silver.etl_batch_control` | same | `transactions`, `channel` (and all marts, per `nightly.yaml:90-97`) |
| `business_views` | 3 | `gold.v_customer_portfolio`, `gold.v_credit_risk`, `gold.v_channel_trend` | none (6.2) | `customer_segment`, `channel`, `dormant`, `reference` |
| `reports` | 3 | `gold.report_*` (4) | none (6.2) | `branch_league`, `card_category`, `monthly_balance`, `business_views` |

Dependency note: legacy `dim_account` is used *before* dormancy recomputation by jobs 02 (FK check) and is otherwise
consumed only after job 03; no consumer reads `dormant_flag` except `v_customer_portfolio.sql:10`. [inference]
`transactions` can therefore validate accounts against `bronze.dim_account` (or a typed pass-through) to avoid a cycle
with `dormant`.
