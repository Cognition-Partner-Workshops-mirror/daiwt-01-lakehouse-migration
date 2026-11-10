# Builder notes by unit

These notes summarise the [business rules](business_rules.md), [target model](target_model.md), [orchestration mapping](orchestration_mapping.md) and [shared macros](shared_macros.md) catalogues for each unit. Source paths are relative to `daiwt-01-teradata-warehouse`. Every note describes legacy semantics to mirror exactly on D0; none of them changes a business calculation.

Conventions that apply to every unit:

- Parity is an exact multiset comparison against `outputs/D0/<unit>/<table>.csv` in the golden contract: `\N` for NULL, fixed decimal scale, `DATE` as `YYYY-MM-DD`, and `TIMESTAMP(0)` rendered as UTC `YYYY-MM-DDTHH:MM:SS.000Z`. `load_ts` is excluded from parity (`manifest.json` `excluded_from_parity`), but the column must still exist in DDL order.
- Columns the legacy INSERT/MERGE does not list take their values from DDL defaults (`legacy/ddl/*.sql`, `data/column_defaults.json`): for example `batch_id 20250630`, `src_system_cd 'COREBANK'`, `record_status_cd 'A'`, `effective_date 2024-01-01` and `expiry_date 9999-12-31`. Emit these as explicit literals or parameters; Delta has no DDL defaults on the insert path the units use.
- Hard-coded literals (`DATE '2025-06-30'`, `DATE '2024-01-01'`, `20250630`) become job/unit parameters (`as_of_date`, `window_start`, `batch_id`) whose D0 values equal the literals ([business rules §2](business_rules.md#2-hardcoded-literals-and-parameterisation)).
- Each BTEQ reject SELECT (`.IF ACTIVITYCOUNT > 0 THEN .QUIT 12`) becomes a pre-write `raise_error` assertion: the whole delivery fails, and nothing is written ([orchestration §5](orchestration_mapping.md#5-control-checks---raise_error)).
- Use Teradata `ADD_MONTHS` semantics through Spark `add_months` (both clamp to month end); never use an interval or day arithmetic for month windows.
- `ROUND`/`CAST` to `DECIMAL(p,s)` is HALF_UP in Spark, which matches the goldens. Never use `bround`.

## Wave 1: platform

### bronze_landing (bronze)
- Land the 11 D0 feeds from `inputs/D0/<db>.<table>/` (pipe-delimited CSV with header; parquet sibling) into `bronze.<table>`, all columns `STRING`. Overwrite per drop, because the legacy staging feeds are replaced rather than appended (`README.md`).
- Check row counts and SHA-256 against `inputs/D0/_manifest.json` before publishing.
- Keep raw bytes: no trimming and no NBSP replacement. Silver owns cleansing (for example `customer_name` U+00A0 handling).
- Read from the Terraform-owned volume `landing.bronze_landing`. Golden inputs are read-only and are never written. Batch `read_files` needs no checkpoint volume. Auto Loader would need one, and none exists in Terraform today ([shared macros §6](shared_macros.md#6-volumes-and-other-uc-objects-the-bundle-needs-prod)).

### sql_macros (platform)
- Owns suffix-scoped SQL UDFs for the repeated expressions in [shared macros](shared_macros.md): channel normalisation, comma-decimal money cast, month start/end, ADD_MONTHS windows, ZEROIFNULL and NULLIFZERO.
- Channel normalisation differs between scripts. `02_transactions.btq:33` uses `UPPER(TRIM())`, while `macros/channel_daily.sql` uses `TRIM` only. Keep two separate templates; do not unify them.
- Changing a shared macro selects every registered unit in CI.

### silver_pipeline (platform)
- Port the daiwt-03 `silver_resources.py` Python-for-DABs scaffold. Wave 2 entity units drop in their own SQL without editing shared files.
- Lakeflow SQL must not use `CREATE OR REFRESH TEMPORARY VIEW`. Use `CREATE OR REFRESH PRIVATE MATERIALIZED VIEW` instead. MVs with `TIMESTAMP_NTZ` columns need `TBLPROPERTIES ('delta.feature.timestampNtz'='supported')`.
- Fail-closed validations (reject delivery) belong in a SQL task before the write, or in `EXPECT ... ON VIOLATION FAIL UPDATE`. A drop-row expectation would silently diverge from legacy.

### governance (platform)
- Port the feed/table `COMMENT ON` text from `legacy/ddl/*.sql` as UC comments. Grant `USE`/`SELECT` to consumers on gold only.
- Grant checks must find this bundle's jobs through `databricks bundle summary`, not by listing workspace jobs by name.
- Never grant outside `daiwt_01_mig_dev` / `daiwt_01_mig_prod`.

### orchestration (platform)
- One job `${var.job_name_prefix}nvb_nightly` with `run_job_task` per unit in the legacy linear order. Separately, the registry `depends_on` captures the narrower data dependencies ([orchestration §2](orchestration_mapping.md#2-target-one-lakeflow-job-dag)).
- `max_concurrent_runs: 1` with queueing and `max_retries: 0`, mirroring the `nvb_nightly_exclusive` lock and the runbook's "restart from upstream" rule.
- Do not register the job as a unit (no `src/sql/units/<unit>/unit.json`). `ci/run_units.py` runs every registered unit, so registering it would run every unit a second time.
- Europe/Oslo schedule, `PAUSED` in dev. Use job health `RUN_DURATION_SECONDS` rules for the 03:00 release SLA and the per-job SLAs. Keep `concurrency: cancel-in-progress: false` in every workflow.
- `batch_open` and `batch_fail` tasks own the OPEN/RUNNING/FAILED transitions of `silver.etl_batch_control`. Only `close_batch` sets `COMPLETE`.

## Wave 2: silver (one unit per entity)

### branch, product, merchant, calendar, card, daily_balance, loan, product_order (silver)
- These are typed copies of the directly loaded `nvb_edw` reference tables at DDL types and scale. For example, a feed value `'0'` must become `0.00` in `DECIMAL(18,2)` columns. There is no golden; check them against `controls/D0/expected/nvb_edw.<table>.json` metrics.
- Keep `record_status_cd`. Downstream units filter `= 'A'` (all D0 rows are `A`, so D0 does not exercise the filter).
- `daily_balance`: `balance_date` drives the month-coverage control in `monthly_balance`. Keep every calendar day (547 per account on D0).
- `card` and `product_order` have no downstream reader in the nightly path ([inventory §7](inventory.md#7-orphans-and-unpopulated-columns)). Build them for completeness and keep them out of mart logic.

### customer → `silver.dim_customer` (golden `outputs/D0/customer/dim_customer.csv`)
- Use `trim(replace(x, char(160), ' '))`, replacing before trimming (`01_customer.btq:30,32`). Spark `trim` does not strip NBSP.
- Deduplicate with `QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY updated_at DESC, customer_name DESC, town DESC, branch_id DESC) = 1` (`:48-57`). D0 goes from 5,100 to 5,000 rows.
- Reject the whole delivery for NULL/≤0 IDs, blank names, NULL `updated_at`, or a `branch_id` not in `dim_branch` (`:39-44`).
- On D0 the MERGE writes `batch_id`/`load_ts` on matched rows only (`:62-71`); inserted rows take DDL defaults. Both give `20250630` on D0. Emit the default columns (`customer_type_cd`, `src_system_cd`, …) explicitly.
- Parse `updated_at` with `try_to_timestamp(trim(x), 'yyyy-MM-dd HH:mm:ss')` into `TIMESTAMP` (LTZ, UTC session), not `TIMESTAMP_NTZ`. The golden prints it as `…T…000Z`, and NTZ renders without the `Z` ([shared macros §2.3](shared_macros.md#23-timestamp0--timestamp-not-timestamp_ntz)).

### transactions → `silver.fct_transaction` (golden `outputs/D0/transactions/fct_transaction.csv`)
- Amount: `try_cast(replace(trim(amount), ',', '.') AS DECIMAL(18,2))`. Channel: `coalesce(nullif(upper(trim(channel)), ''), 'ONLINE')` (`02_transactions.btq:32-33`).
- Reject the whole delivery for any invalid ID, amount or date, a date outside `2024-01-01..2025-06-30` (inclusive), an unknown account or merchant, a channel outside the four values, or a duplicate `transaction_id` (`:38-48`).
- Validate account FKs against the typed landed account data (`bronze.dim_account`), not `silver.dim_account`. `dormant` rewrites `silver.dim_account` and reads this unit, so reading it here would create a cycle.
- Replace the whole window atomically (`INSERT … REPLACE WHERE txn_date BETWEEN :window_start AND :as_of_date`). BT/ET becomes one Delta commit.
- Six business columns come from the feed; `posting_status_cd`, `reversal_ind`, `currency_cd`, `clearing_scheme_cd`, … are DDL defaults ([business rules §7](business_rules.md#7-ddl-defaults-vs-mergeinsert-column-lists)).

### dormant → `silver.dim_account` (golden `outputs/D0/dormant/dim_account.csv`)
- The flag is `'Y'` when no active transaction has `txn_date > add_months(:as_of, -6) AND txn_date <= :as_of`, which is `> 2024-12-30` on D0. Use `add_months`, not 180 days. D0 gives 105 `Y`.
- Mirror the cursor scope (`flag_dormant.sql:23-46`): only active accounts of customers whose branch is active get `Y`; all other active accounts are reset to `N`.
- `NOT IN` over the transaction subquery returns no rows if the subquery yields a NULL `account_id`. Write it as `NOT IN` with the same NULL behaviour, or prove that `account_id` is non-null. Do not silently switch to `NOT EXISTS`.
- D0 cannot tell `add_months`, 180-day and strict-cutoff variants apart (0 differences). Add unit-test fixtures on the 2024-12-30/31 boundary.
- Reject when an account's `customer_id` is missing from `dim_customer` (`03_dormant.btq:35-37`).

### close_batch → `silver.etl_batch_control` (golden `outputs/D0/close_batch/etl_batch_control.csv`)
- Before writing, compare `COUNT(*)` and `SUM(amount)` of `fct_transaction` with `SUM(txn_count)` and `SUM(net_amount)` of `gold.mart_channel_daily`. Fail if they differ or the mart is empty (`10_close_batch.btq:26-40`).
- Write one row keyed `(batch_date, job_name='NIGHTLY')` with `status 'COMPLETE'`, `last_step_cd '10_close_batch'` and `row_count` = the fact count (180,000 on D0).
- The remaining columns come from DDL defaults: `window_start_date`, `window_end_date`, `reject_count 0`, `scheduler_group_cd 'NVB_NIGHTLY'`, … (`legacy/ddl/nvb_edw.etl_batch_control.sql:11-21`).
- The unit is silver-layer but runs after the gold `channel` unit. It must also work standalone on an empty table in CI.

## Wave 3: gold (one unit per mart, plus business views)

### monthly_balance → `gold.mart_monthly_balance` (golden `outputs/D0/monthly_balance/mart_monthly_balance.csv`)
- `CAST(avg(CAST(balance AS DECIMAL(28,6))) AS DECIMAL(18,2))`, HALF_UP. On D0, 1,894 account-months have a mean exactly on a half cent, so HALF_EVEN would change 967 of 144,000 rows.
- `closing_balance = max(CASE WHEN balance_date = last_day(balance_date) THEN balance END)`.
- Reject unless every account-month has its month-end row and `ledger_days = day(last_day(month))` (`04_monthly_balance.btq:26-37`).
- Inner join to active `silver.dim_account`; the D0 output is 8,000 × 18 = 144,000 rows.

### customer_segment → `gold.mart_customer_segment` (golden `outputs/D0/customer_segment/mart_customer_segment.csv`)
- The window `txn_date BETWEEN date_add(add_months(:as_of, -12), 1) AND :as_of` (2024-07-01..2025-06-30) stays in the LEFT JOIN `ON` clause so that customers without transactions survive.
- Segment rules, first match wins: `DORMANT` if `max(txn_date)` is NULL or `< date_sub(:as_of, 90)` (strict, 2025-04-01); `PREMIUM` if count ≥ 50 and debit spend ≥ 20000; `ACTIVE` if count ≥ 20; otherwise `OCCASIONAL`. D0 counts are 61/221/2327/2391.
- `spend = coalesce(sum(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 0)` (debits only, positive). `txn_count = count(t.transaction_id)`, not `count(*)`.
- Coverage must equal active customers (5,000) or fail.

### branch_league → `gold.mart_branch_performance` (golden `outputs/D0/branch_league/mart_branch_performance.csv`)
- `net_amount = coalesce(sum(t.amount), 0)`, signed, over active accounts and transactions with `txn_date BETWEEN window_start AND :as_of` in the `ON` clause.
- `league_rank = rank() OVER (ORDER BY coalesce(sum(t.amount), 0) DESC)`. Ties share a rank (there are none on D0), so do not use `row_number`.
- `customer_count = count(DISTINCT customer_id)`. The row count must equal the number of distinct active branches among customers (8).

### product_profitability → `gold.mart_product_profitability` (golden `outputs/D0/product_profitability/mart_product_profitability.csv`)
- Take one mean over all active account-days per product, `avg(CAST(balance AS DECIMAL(28,6)))`. It is not a mean of account means.
- `annual_income = CAST(avg_unrounded * annual_rate / 100 AS DECIMAL(18,2))`. Compute it from the unrounded mean, not from `average_balance`.
- Reject a NULL or negative rate, or a NULL balance (`07_product_profitability.btq:39-40`). `annual_rate = max(p.annual_rate)`.
- The output is 4 rows; D0 values include product 1 at `397757.71` / `5966.37`.

### channel → `gold.mart_channel_daily` (golden `outputs/D0/channel/mart_channel_daily.csv`)
- Group by `txn_date` and `coalesce(nullif(trim(channel), ''), 'ONLINE')` (TRIM, no UPPER; `channel_daily.sql:18-28`).
- Inner join `silver.dim_calendar`, and reject if any transaction date is missing from the calendar (`08_channel.btq:33-35`).
- `txn_count = count(*)`, `net_amount = coalesce(sum(amount), 0)`. The output is 2,188 rows.
- `close_batch` reconciles against this mart, so totals must equal `fct_transaction` (180,000 rows).

### card_category → `gold.mart_card_spend_category` (golden `outputs/D0/card_category/mart_card_spend_category.csv`)
- Filter `channel = 'CARD' AND amount < 0`, then **inner** join active `dim_merchant`. `spend = sum(-amount)`, `txn_count = count(transaction_id)`.
- `coalesce(m.category, 'UNCLASSIFIED')` is kept even though D0 never reaches it. Keep the inner join.
- The mart has no join to `dim_card`; mirror that (`card_category.sql:17-29`).
- The output is 4 rows: FUEL, GROCERY, RETAIL and TRAVEL.

### business_views → `gold.v_customer_portfolio`, `gold.v_credit_risk`, `gold.v_channel_trend`, `gold.report_*`
- There is no golden. Reconcile each `gold.report_*` view row-for-row against the legacy `reports/*.sql` text run over the migrated marts, and reconcile the views against the recomputations in [target model §6](target_model.md#6-business-views-and-reports-no-golden).
- `v_channel_trend` uses Teradata `CSUM`/`MAVG`. Translate them to `sum(...) OVER (ORDER BY txn_date ROWS UNBOUNDED PRECEDING)` and `avg(...) OVER (ORDER BY txn_date ROWS 6 PRECEDING)`. The view covers the `MOBILE` channel only, as in `legacy/views/v_channel_trend.sql`.
- `NULLIFZERO(x)` becomes `nullif(x, 0)` and `ZEROIFNULL(x)` becomes `coalesce(x, 0)`. The share-of-spend in `reports/card_services.sql` is `spend / nullif(sum(spend) OVER (), 0)`.
- `reports/finance_monthly.sql` filters `EXTRACT(YEAR FROM month_start) = 2025`. Keep that filter as a parameter whose D0 value is 2025.
