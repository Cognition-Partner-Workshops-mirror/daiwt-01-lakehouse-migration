# Orchestration mapping: legacy nightly schedule -> one Lakeflow job

Wave 0 discovery for the `orchestration` platform unit of bundle `daiwt-01-migration`.

Citation prefixes: `L:` = legacy repo `daiwt-01-teradata-warehouse` @ `cf4b4fa`; `M:` = this repo @ `bfb55fa` (`main`); `G:` = golden volume `/Volumes/daiwt_01_mig_prod/landing/golden/` (capture `20261110T162154Z`); `R:` = reference repo `daiwt-03-lakehouse-migration` `main`.

Each section separates **Verified** (read in source or recomputed against D0 golden CSVs) from **Design / inference** (proposed target; nothing below exists in this repo yet — `M:src/sql/units/registry.json` is `{}` and `M:databricks.yml:1-61` defines schemas only, no jobs).

## 1. Legacy schedule inventory

### Verified
- Global settings: timezone `Europe/Oslo` (`L:legacy/schedules/nightly.yaml:1`), business date `'2025-06-30'` (`:2`), `calendar: every_day` (`:3`), `concurrency: 1` (`:4`), `release_sla: '03:00'` (`:5`), `on_failure: hold_descendants_and_page_owner` (`:6`), `lock_resource: nvb_nightly_exclusive` (`:7`), retention `request_logs: 90`, `control_reports: 400` days (`:8-10`).
- Twelve jobs, a strict linear chain (each `depends_on` names the previous job). All BTEQ jobs accept only return code `0`.

| Legacy job | Lines | Start | SLA | Owner | Script | Restart note (verbatim intent) |
|---|---|---|---|---|---|---|
| `upstream` | `nightly.yaml:12-17` | 00:30 | 00:55 | Core Banking Operations | none | replace all upstream tables + raw deliveries; verify manifest |
| `01_customer` | `:18-25` | 01:00 | 01:10 | Customer Data Operations | `legacy/bteq/01_customer.btq` | rerun merge from retained raw delivery |
| `02_transactions` | `:26-33` | 01:10 | 01:25 | Payments Data Operations | `02_transactions.btq` | rerun full retained window; rollback protects prior load |
| `03_dormant` | `:34-41` | 01:25 | 01:30 | Deposit Operations | `03_dormant.btq` | recalc all active accounts after payments restored |
| `04_monthly_balance` | `:42-49` | 01:30 | 01:50 | Finance Control | `04_monthly_balance.btq` | rebuild all retained month partitions |
| `05_customer_segment` | `:50-57` | 01:50 | 02:00 | Customer Insight | `05_customer_segment.btq` | replace segment population; verify full coverage |
| `06_branch_league` | `:58-65` | 02:00 | 02:10 | Branch Operations | `06_branch_league.btq` | rebuild after customer and payment checks pass |
| `07_product_profitability` | `:66-73` | 02:10 | 02:20 | Product Finance | `07_product_profitability.btq` | replace in one transaction; inspect rates first |
| `08_channel` | `:74-81` | 02:20 | 02:25 | Digital Operations | `08_channel.btq` | rebuild date partitions; compare counts/amounts |
| `09_card_category` | `:82-89` | 02:25 | 02:30 | Card Services | `09_card_category.btq` | replace category totals; compare negative card payments |
| `10_close_batch` | `:90-97` | 02:30 | 02:40 | Data Operations | `10_close_batch.btq` | rerun only after every preceding job passes |
| `reconcile` | `:98-103` | 02:40 | 03:00 | Finance Control | none | withhold consumer publication until sign-off |

- Runbook rules (`L:legacy/schedules/runbook.md`): all times Europe/Oslo (`:3`); confirm deposit-ledger and payment-clearing share the business date before job 01 (`:3-4`); bulk-load upstream and retain manifest counts (`:4-5`); DDL at install only, macros/procedures before BTEQ (`:6`); stop on nonzero return code and hold dependants (`:8-9`, `:34-35`); compare controls before publishing (`:10-11`); full restart = reload deliveries then rerun 01..10 in order (`:13-14`); summary builds replace content (`:14`); no concurrent copies (`:14-15`, `:30`); failed delete/insert build -> mart unavailable until rebuilt (`:15-16`); preserve prior reporting snapshot until reconciliation approved (`:17-18`, `:44-46`).

## 2. Target: one Lakeflow job DAG

### Design / inference
One scheduled job resource, key `nvb_nightly`, name `${var.job_name_prefix}nvb_nightly` (prefix variable `M:databricks.yml:19-21`, emptied in prod `:58`). It orchestrates the registered per-unit jobs with `run_job_task` (pattern of `R:docs/discovery/orchestration_mapping.md`), so unit SQL lives once in each unit job and CI keeps running unit jobs directly (section 9). Legacy order is preserved as a strict chain because the runbook full-restart rule is "01 through 10 in order" (`L:runbook.md:13-14`); the registry `depends_on` carries the narrower data dependencies (table in 2.2) and is what CI uses.

```
batch_open -> bronze_landing -> customer -> transactions -> dormant -> monthly_balance
  -> customer_segment -> branch_league -> product_profitability -> channel
  -> card_category -> close_batch -> release_export -> reconcile -> business_views
batch_fail (run_if: AT_LEAST_ONE_FAILED, depends on every task above except business_views)
```

### 2.1 Task table

| Task key | Kind | Replaces (legacy) | Nightly `depends_on` | `run_if` | Unit job / SQL |
|---|---|---|---|---|---|
| `batch_open` | `sql_task` | register/RUNNING block at top of every BTEQ (`L:legacy/bteq/01_customer.btq:8-24`) | — | `ALL_SUCCESS` | orchestration-owned SQL (section 4) |
| `bronze_landing` | `run_job_task` | `upstream` (`nightly.yaml:12-17`), manifest check (`runbook.md:4-5`) | `batch_open` | `ALL_SUCCESS` | platform unit `bronze_landing` |
| `customer` | `run_job_task` | `01_customer` | `bronze_landing` | `ALL_SUCCESS` | contract unit -> `silver.dim_customer` |
| `transactions` | `run_job_task` | `02_transactions` | `customer` | `ALL_SUCCESS` | -> `silver.fct_transaction` (+ `02_transactions` control row) |
| `dormant` | `run_job_task` | `03_dormant` | `transactions` | `ALL_SUCCESS` | -> `silver.dim_account` |
| `monthly_balance` | `run_job_task` | `04_monthly_balance` | `dormant` | `ALL_SUCCESS` | -> `gold.mart_monthly_balance` |
| `customer_segment` | `run_job_task` | `05_customer_segment` | `monthly_balance` | `ALL_SUCCESS` | -> `gold.mart_customer_segment` |
| `branch_league` | `run_job_task` | `06_branch_league` | `customer_segment` | `ALL_SUCCESS` | -> `gold.mart_branch_performance` |
| `product_profitability` | `run_job_task` | `07_product_profitability` | `branch_league` | `ALL_SUCCESS` | -> `gold.mart_product_profitability` |
| `channel` | `run_job_task` | `08_channel` | `product_profitability` | `ALL_SUCCESS` | -> `gold.mart_channel_daily` |
| `card_category` | `run_job_task` | `09_card_category` | `channel` | `ALL_SUCCESS` | -> `gold.mart_card_spend_category` |
| `close_batch` | `run_job_task` | `10_close_batch` | `card_category` | `ALL_SUCCESS` | -> `silver.etl_batch_control` |
| `release_export` | `sql_task` | `nightly_release.txt` export (`10_close_batch.btq:51-53`) | `close_batch` | `ALL_SUCCESS` | section 6 |
| `reconcile` | `sql_task` | `reconcile` (`nightly.yaml:98-103`) | `release_export` | `ALL_SUCCESS` | section 8 |
| `business_views` | `run_job_task` | `legacy/views/*.sql`, `reports/*.sql` | `reconcile` | `ALL_SUCCESS` | Wave 3 views unit (section 8) |
| `batch_fail` | `sql_task` / python | `failed:` label of every BTEQ (e.g. `01_customer.btq:81-87`) | all above except `business_views` | `AT_LEAST_ONE_FAILED` | section 4 |

`sql_macros` and `governance` are platform units deployed/run by CI (section 9), not nightly tasks: the runbook deploys macros/procedures before BTEQ as an install step (`L:runbook.md:6`), not as a scheduled job.

### 2.2 Data dependencies for the registry (`depends_on`)
Derived from the tables each BTEQ/proc/macro reads (inference from the scripts cited; owner of each table = the contract unit that outputs it).

| Unit | Reads | Registry `depends_on` |
|---|---|---|
| `customer` | customer feed, branch dim (`01_customer.btq:28-60`) | `bronze_landing` |
| `transactions` | transaction feed, account + merchant dims (`02_transactions.btq:28-63`) | `bronze_landing` |
| `dormant` | accounts, transactions (`03_dormant.btq:28-50`) | `customer`, `transactions` |
| `monthly_balance` | daily balances, accounts (`04_monthly_balance.btq:28-47`) | `dormant` |
| `customer_segment` | customers, accounts, transactions (`05_customer_segment.btq:28-45`) | `customer`, `transactions`, `dormant` |
| `branch_league` | customers, transactions, branches (`06_branch_league.btq:28-45`) | `customer`, `transactions`, `dormant` |
| `product_profitability` | daily balances, accounts, products (`07_product_profitability.btq:28-52`) | `dormant` |
| `channel` | transactions (`08_channel.btq:28-45`) | `transactions` |
| `card_category` | transactions, merchants (`09_card_category.btq:28-44`) | `transactions` |
| `close_batch` | transactions, channel mart (`10_close_batch.btq:28-42`) | `transactions`, `channel` |

## 3. Concurrency, restart and repair

### Verified
- One copy at a time: `concurrency: 1` + `lock_resource: nvb_nightly_exclusive` (`nightly.yaml:4,7`; `runbook.md:14-15,30`).
- Restart at the failed job only when all earlier outputs are intact, otherwise at job 01; same inputs; do not advance control date, batch id or logical load timestamp (`runbook.md:38-40`). No automatic skip based on a prior `COMPLETE` row (`runbook.md:32`).
- Replacement semantics: `02` and `07` wrap delete/insert in `BT;`/`ET;` (`02_transactions.btq:53-64`, `07_product_profitability.btq:43-53`; `runbook.md:42`); macros replace their slice as one request (`runbook.md:43`); procedures can leave an unavailable slice (`runbook.md:44-45`).

### Design / inference
- `max_concurrent_runs: 1` and `queue.enabled: true` on `nvb_nightly` (a second trigger waits instead of overlapping, the closest analogue to a held lock). Every unit job also gets `max_concurrent_runs: 1` so a CI replay of one unit cannot overlap the same unit under the nightly job. `max_retries: 0` on every task (legacy has no automatic retry; operator decides).
- Cross-job lock: prod unit jobs are also run by `deploy-prod.yml` (`M:.github/workflows/deploy-prod.yml:29-38`). `batch_open` should fail with `raise_error` if `silver.etl_batch_control` already shows `RUNNING` for the business date, and CI should not deploy during the 00:30-03:00 Oslo window (open question).
- Repair run = "restart at failed job": Lakeflow repair re-executes the failed task and its descendants inside the same run, reusing the run's job parameters, so `business_date`/`batch_id`/`load_ts` cannot drift (`runbook.md:40`). A new full run = "restart at job 01" when earlier outputs are not intact.
- Every unit is an idempotent full replacement of its slice (`CREATE OR REPLACE TABLE ... AS` or `INSERT OVERWRITE`/`REPLACE WHERE`), which a Delta commit makes atomic, so a failed unit leaves the previous table version intact — this preserves the BT/ET guarantee for all units, including the procedure-based ones (`runbook.md:44-46`).
- Pre-write checks run before the write; post-write checks (legacy validated after `CALL`/`EXEC`) are evaluated against the candidate result in a temp view **before** replacing the target, so a failing post-check never publishes a partial population (`runbook.md:46`).

## 4. Batch control lifecycle (`silver.etl_batch_control`)

### Verified
- Every BTEQ: look up `NIGHTLY` row for the date (`01_customer.btq:8-10`), insert `('2025-06-30','NIGHTLY','OPEN',0)` if missing (`:12-17`), set `RUNNING` + `last_step_cd = '<job>'` and require exactly one row (`:21-24`). Same block at lines 8-24 of `02`..`10`.
- On failure: set `FAILED` + `last_step_cd = '<job>'`, `.QUIT 12`; `.QUIT 99` if that update fails (`01_customer.btq:83-87`; `10_close_batch.btq:63-67`; `runbook.md:34-35`).
- Only job 10 sets `COMPLETE`, with `row_count = payment_count` from `vt_close_control` (`10_close_batch.btq:44-48`; `runbook.md:29`), after checking transaction count/amount against `mart_channel_daily` totals (`10_close_batch.btq:28-42`).
- Remaining columns come from DDL defaults (`L:legacy/ddl/nvb_edw.etl_batch_control.sql:11-21`): `batch_id 20250630`, `load_ts 2025-06-30 23:00:00`, `src_system_cd COREBANK`, `record_status_cd A`, `effective_date 2024-01-01`, `expiry_date 9999-12-31`, `last_step_cd 10_close_batch`, window `2024-01-01..2025-06-30`, `reject_count 0`, `scheduler_group_cd NVB_NIGHTLY`. `load_ts` is fixed for repeatable restarts (`:31-32`).
- Golden row (`G:outputs/D0/close_batch/etl_batch_control.csv`, 1 row): `2025-06-30,NIGHTLY,COMPLETE,180000,20250630,COREBANK,A,2024-01-01,9999-12-31,10_close_batch,2024-01-01,2025-06-30,0,NVB_NIGHTLY`. Reference control: `L:golden/compute_baselines.py:106` (`row_count = len(t)`).
- Recomputed from D0 goldens: `fct_transaction.csv` = 180,000 rows, 180,000 distinct `transaction_id`, `SUM(amount) = 90964750.88`, `txn_date` 2024-01-01..2025-06-30; `mart_channel_daily.csv` (2,188 rows) `SUM(txn_count) = 180000`, `SUM(net_amount) = 90964750.88`. So the job-10 control holds on D0 and `row_count = 180000` reproduces.
- Not in the golden: initial `OPEN`/`RUNNING` transitions and failure branches (`G:skipped_branches.json`).

### Design / inference
- `batch_open`: `MERGE` the `(business_date,'NIGHTLY')` row: insert `OPEN` if missing, then set `RUNNING`, `last_step_cd = 'batch_open'`. No skip on `COMPLETE` (`runbook.md:32`).
- `last_step_cd` per step: unit jobs stay free of batch-control writes so standalone CI runs are parity-clean; the authoritative per-step history is the Lakeflow run (legacy: request log is the per-job history, `runbook.md:31`). `batch_fail` (`run_if: AT_LEAST_ONE_FAILED`) sets `FAILED` and `last_step_cd` = the first failed task key, read from the parent run (`{{job.run_id}}`) via the Jobs API.
- `close_batch` unit (owns the table): `CREATE TABLE IF NOT EXISTS`, run the count/amount control with `raise_error`, then `MERGE` the row with `status='COMPLETE'`, `last_step_cd='10_close_batch'`, `row_count = COUNT(*)` of `silver.fct_transaction`, and every DDL-default column set explicitly from job parameters. Run standalone (CI, empty table) it inserts the row directly, which matches the golden; under the nightly job it updates the `RUNNING` row. `load_ts` = `load_ts` parameter (excluded from parity).

## 5. Control checks -> `raise_error`

### Verified
Every BTEQ validation `SELECT` prints offending keys then branches to `rejected` when `ACTIVITYCOUNT > 0` (`runbook.md:36-37`). Inventory:

| Unit | Legacy check | Lines | When |
|---|---|---|---|
| `customer` | invalid/unmatched customer or branch | `01_customer.btq:39-46` | pre-write |
| `transactions` | invalid id/amount/date window/account/merchant/channel | `02_transactions.btq:38-47` | pre-write |
| `transactions` | duplicate `transaction_id` | `02_transactions.btq:48-50` | pre-write |
| `dormant` | account control | `03_dormant.btq:35-39` | pre-write |
| `dormant` | dormant flag vs `ADD_MONTHS(...,-6)` window | `03_dormant.btq:42-50` | post-write |
| `monthly_balance` | incomplete ledger days per account-month | `04_monthly_balance.btq:34-39` | pre-write |
| `monthly_balance` | month control | `04_monthly_balance.btq:42-47` | post-write |
| `customer_segment` | `customer_id <= 0` | `05_customer_segment.btq:34-36` | pre-write |
| `customer_segment` | coverage of active customers | `05_customer_segment.btq:39-45` | post-write |
| `branch_league` | branch population | `06_branch_league.btq:32-36` | pre-write |
| `branch_league` | branch totals | `06_branch_league.btq:39-45` | post-write |
| `product_profitability` | product income/rate | `07_product_profitability.btq:39-42` | pre-write |
| `channel` | channel control | `08_channel.btq:33-37` | pre-write |
| `channel` | channel totals vs control | `08_channel.btq:40-45` | post-write |
| `card_category` | `spend < 0 OR category IS NULL` | `09_card_category.btq:35-37` | pre-write |
| `card_category` | category totals vs control | `09_card_category.btq:40-44` | post-write |
| `close_batch` | count/amount vs channel mart | `10_close_batch.btq:32-42` | pre-write |
| all | batch row `ACTIVITYCOUNT <> 1` | `*.btq:24`, `10_close_batch.btq:48` | state |

### Design / inference
- Each check becomes one statement in the unit SQL: `SELECT raise_error(concat('<unit>: <check> failed, keys=', array_join(slice(collect_list(key),1,20), ','))) FROM (<legacy predicate>) HAVING count(*) > 0`. The error message carries the offending keys into the task run output, replacing the request-log listing (`runbook.md:36`). Failure -> task fails -> descendants held (`run_if: ALL_SUCCESS`).
- If a Silver unit is built as a Lakeflow pipeline (Wave 1 scaffold), the same predicates become `EXPECT ... ON VIOLATION FAIL UPDATE`, never `DROP ROW`, since no invalid rows may be silently discarded (`runbook.md:37`).
- Informational `SELECT`s (e.g. `03_dormant.btq:53`, `04_monthly_balance.btq:50`) are activity reports only; they become optional task output, not checks.

## 6. Exports

### Verified
- `control_reports/02_transactions.txt`: payment count, net amount, date bounds (`02_transactions.btq:68-73`; `runbook.md:23-24`).
- `control_reports/nightly_release.txt`: `batch_date, job_name, status, row_count, last_step_cd, load_ts` from the batch row (`10_close_batch.btq:51-55`).
- Directory created by the scheduler (`runbook.md:22`; `L:README.md:94`); retained 400 days with manifest + Finance Control approval (`runbook.md:24-25`; `nightly.yaml:10`).
- Neither file is in the golden capture (`G:skipped_branches.json`). Prod volumes today: only `landing.golden` and `landing.bronze_landing` (read-only `information_schema.volumes` query on `daiwt_01_mig_prod`).

### Design / inference
- Primary target: Delta table `silver.nightly_control_report` (owned by the orchestration unit), one row per report per run: `report_name`, `business_date`, `batch_id`, `run_id`, report columns, `created_at`. `transactions` cannot write it in CI without coupling, so `release_export` writes both rows after `close_batch` (the `02` figures are recomputable from `silver.fct_transaction`). Retention: nightly `DELETE WHERE created_at < current_date - INTERVAL 400 DAYS`.
- Optional file rendition (`.txt` for Finance Control): a new UC volume, e.g. `landing.control_reports`. `landing.golden` must not be written and `landing.bronze_landing` is an input area, so this needs a new volume provisioned outside this bundle (open question).

## 7. Schedule, SLA, notifications, retention, parameters

### Verified
- Dev schedules paused: `presets.trigger_pause_status: PAUSED` (`M:databricks.yml:26-27`); prod is `mode: production` (`:47`).
- SLA durations from `nightly.yaml` start->sla: upstream 25 min, 01 10, 02 15, 03 5, 04 20, 05 10, 06 10, 07 10, 08 5, 09 5, 10 10, reconcile 20; release 03:00 is 150 min after the 00:30 start (`nightly.yaml:5,13-16,...,99-102`).
- Hardcoded literals: `DATE '2025-06-30'` in every batch-row statement (`*.btq:10,17,22`), `batch_id = 20250630` and `load_ts TIMESTAMP '2025-06-30 23:00:00'` (`01_customer.btq:67`), window literals `2024-01-01`/`2025-06-30` (`02_transactions.btq:43,56`; `04_monthly_balance.btq:40`; `07_product_profitability.btq:35`; `08_channel.btq:38`; `09_card_category.btq:38`), proc arguments (`03_dormant.btq:40`; `05_customer_segment.btq:37`; `06_branch_league.btq:37`), slice deletes `batch_id = 20250630` (`L:legacy/procs/branch_league.sql:20`; `customer_segment.sql:20`; `legacy/macros/card_category.sql:8-9`), DDL defaults (`etl_batch_control.sql:11-21`). README: literals and procedure arguments "must be advanced together" (`L:README.md:29-30`).
- Derived dates: dormancy uses `ADD_MONTHS(...,-6)` (`03_dormant.btq:47`; `runbook.md:48-50`), recent-payment start 2024-12-31 (`compute_baselines.py:71`), segmentation 2024-07-01..2025-06-30 and stale `< 2025-04-01` (`compute_baselines.py:87,91`; `runbook.md:50-51`).

### Design / inference
- Schedule on `nvb_nightly`: `quartz_cron_expression: "0 30 0 * * ?"`, `timezone_id: Europe/Oslo`. Dev: paused by the preset. Prod: `pause_status` from a variable defaulting `PAUSED` until cutover is agreed (otherwise prod replays every night; open question).
- SLA: job-level `health.rules: [{metric: RUN_DURATION_SECONDS, op: GREATER_THAN, value: 9000}]` (03:00 release) with `on_duration_warning_threshold_exceeded`; task-level health rules with the per-job durations above. Legacy SLAs page, they do not kill work, so `timeout_seconds` is a generous hard stop (e.g. 3x SLA), not the SLA.
- `hold_descendants_and_page_owner`: hold = default `run_if: ALL_SUCCESS`; page = job `email_notifications.on_failure` / `webhook_notifications.on_failure` to a notification-destination variable (owner groups per `nightly.yaml` owner column); `batch_fail` also records state.
- Retention: request logs 90 days -> task run output/system tables are platform-retained; to guarantee 90 days, `batch_fail`/`release_export` persist run id, task outcomes and error text into the control table. Control reports 400 days -> section 6.
- Job parameters (defaults = D0): `drop=D0`, `business_date=2025-06-30`, `batch_id=20250630`, `load_ts=2025-06-30 23:00:00`, `window_start=2024-01-01`. Derived in SQL: `window_end=business_date`, dormancy cutoff `add_months(business_date,-6)`, segmentation start `date_add(add_months(business_date,-12),1)`, stale threshold `date_sub(business_date,90)` (= 2025-04-01 on D0). Unit jobs declare the same parameters with D0 defaults because `ci/run_units.py` passes only `drop` (`M:ci/run_units.py:16-20`); `nvb_nightly` forwards them via `run_job_task.job_parameters`.

## 8. Off-workflow work: re-homing

| Legacy item | Source | Decision | Rationale |
|---|---|---|---|
| `upstream` bulk load + manifest check | `nightly.yaml:12-17`; `runbook.md:4-5` | task `bronze_landing` (platform unit) | loads `inputs/<drop>` feeds; `raise_error` if row counts differ from the manifest |
| Same-business-date confirmation | `runbook.md:3-4` | check inside `bronze_landing` | automatable predicate on feed dates |
| DDL install | `runbook.md:6` | bundle schemas (`M:databricks.yml:32-45`) + unit DDL | install-time, not scheduled |
| Macros/procedures deploy | `runbook.md:6` | platform unit `sql_macros`, run by CI on deploy | install-time |
| `10_close_batch` | `nightly.yaml:90-97` | task `close_batch` | contract unit |
| `reconcile` | `nightly.yaml:98-103`; `runbook.md:10-11,17-18` | task `reconcile`: automated comparison of the two control-report rows vs `etl_batch_control`; human Finance Control sign-off out of scope | the sign-off is a manual approval with no script |
| Preserve prior reporting snapshot | `runbook.md:17,44-45` | out of scope for Wave 1; Delta history gives a rollback point | consumers read `gold` directly in this demo |
| Views and reports | `legacy/views/*.sql`, `reports/*.sql` | Wave 3 `business_views` unit, task after `reconcile` | views are `CREATE OR REPLACE VIEW`, idempotent; no golden output |
| Credential rendering (`TD_*`) | `L:README.md:30-32`; `runbook.md:8` | out of scope | replaced by bundle identity/OIDC (`M:.github/workflows/deploy-prod.yml`) |
| Escalation to Finance Control/Data Ops | `runbook.md:18` | failure notifications | section 7 |

## 9. Coexistence with CI

### Verified
- PR: offline pytest and `bundle validate --strict -t dev --var schema_suffix=pr_<n>`, no deploy/run (`M:.github/workflows/pr.yml:20-21,35`), then attestation check (`:56`).
- Dev verify: deploy dev, clean selected units, `ci/run_units.py` per drop, parity, record attestation for HEAD (`M:ci/dev_verify.sh:5-8,32-52`).
- Prod: on push to `main`, validate + deploy prod (`deploy-prod.yml:27-28`), then `run_units.py prod --target prod --drop $drop` and parity per drop (`deploy-prod.yml:29-38`).
- `run_units.py` runs registered `job` keys in topological order with only `--params drop=<drop>`; dev suffix must be `pr_<n>`, prod `prod` (`M:ci/run_units.py:9-20`).
- Classification: `docs/`, `tests/`, `README`, `.github/` select nothing; `src/sql/units/<unit>/` or `resources/<unit>.job.yml` select that unit + dependants; any other path or `registry.json` selects all (`M:ci/classify_changes.py:21-31`; `M:docs/registry.md`).
- Permissions: prod top-level `users CAN_VIEW` + deployer `CAN_MANAGE` (`M:databricks.yml:48-52`); dev has no `permissions` block (`:24-45`). Reference adds the same block to dev (`R:databricks.yml:37-42`).

### Design / inference
- `nvb_nightly` is **not** in `registry.json`: registered entries are executed by `run_units.py`, so registering it would execute every unit twice. Its file (e.g. `resources/nvb_nightly.job.yml`) matches no unit, so changing it selects all units (`classify_changes.py:28-29`) — the right blast radius for a DAG change.
- Unit job keys = contract unit names (`customer` … `close_batch`) plus platform keys `bronze_landing`, `sql_macros`, `governance`, each named `${var.job_name_prefix}<key>`; `nvb_nightly` references them as `${resources.jobs.<key>.id}`.
- Because Wave 2/3 builders land units in parallel, a hand-edited DAG file is a merge hotspot and `bundle validate` fails if it references a job that does not exist yet. Recommendation: generate `nvb_nightly` tasks from `registry.json` (Python-for-DABs, as the reference does for its silver pipeline) using the fixed legacy order in 2.1 and skipping unregistered units; or land it after Wave 3.
- `databricks.yml` has no `include:` yet (`M:databricks.yml:1-61`); the orchestration unit adds it.
- Permissions: rely on target-level `users CAN_VIEW`; add the same block to `dev` so PR jobs are viewable.

## 10. Open questions
1. Prod schedule `PAUSED` or active after cutover?
2. Provision a `landing.control_reports` volume for file exports, or table-only?
3. Notification destination for page-owner (email group / webhook)?
4. Should `deploy-prod.yml` avoid the 00:30-03:00 Oslo window?
5. Generated DAG (from registry) vs hand-written file landing after Wave 3?
