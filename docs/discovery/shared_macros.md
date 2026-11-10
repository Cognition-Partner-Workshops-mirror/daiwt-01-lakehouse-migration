# DAIWT-01 Discovery: shared library and foundation DDL (`shared_macros.md`)

Wave 0 analysis of the Nordvik Sparebank Teradata 17 estate
(`Cognition-Partner-Workshops-mirror/daiwt-01-teradata-warehouse`, read-only) for the
shared `sql_macros` unit, the bronze/silver/gold table DDL plan and the Unity Catalog
objects the bundle needs. Structure mirrors DAIWT-03 `docs/discovery/shared_macros.md`;
all content is re-derived from this estate.

Conventions:

- Legacy paths are relative to the legacy repo root; migration paths are relative to this
  repo; Terraform paths are relative to `dbx-data-platform-infra`.
- **[V]** = verified, either by reading the cited source or by running the stated query
  read-only on warehouse `6dc4b80538621b7b` against the D0 golden volume
  (`/Volumes/daiwt_01_mig_prod/landing/golden/`). **[I]** = inference or recommendation.
- Target names are medallion names only. The legacy databases map as `nvb_stg` → `bronze`,
  `nvb_edw` → `silver`, `nvb_mart` → `gold`. Dev schemas are `<layer>_pr_<n>`
  (`databricks.yml` `bronze_schema`/`silver_schema`/`gold_schema` variables); prod schemas
  are `bronze`/`silver`/`gold` (`src/parity/parity_check.py:205-211`).

## 1. Schemas

| Legacy database | Target (prod / dev) | Objects | Evidence |
|---|---|---|---|
| `nvb_stg` | `bronze` / `bronze_pr_<n>` | `customer_feed`, `transaction_feed` (raw all-`VARCHAR(80)` feeds) | `legacy/ddl/nvb_stg.customer_feed.sql:1-13`, `legacy/ddl/nvb_stg.transaction_feed.sql:1-14` **[V]** |
| `nvb_edw` | `silver` / `silver_pr_<n>` | 7 dimensions, 4 facts, `etl_batch_control`, 3 macros, 3 procedures | `legacy/ddl/nvb_edw.*.sql`, `legacy/macros/*.sql:1`, `legacy/procs/*.sql:1` **[V]** |
| `nvb_mart` | `gold` / `gold_pr_<n>` | 6 marts, 3 views | `legacy/ddl/nvb_mart.*.sql`, `legacy/views/*.sql:1` **[V]** |
| (none) | `landing` (prod only) | volumes `golden`, `bronze_landing` | `daiwt-01-demo/platform/databricks/main.tf:13,114-122` **[V]** |

The other nine D0 inputs (`dim_account`, `dim_branch`, `dim_calendar`, `dim_card`,
`dim_merchant`, `dim_product`, `fct_daily_balance`, `fct_loan`, `fct_product_order`) are
delivered pre-typed into `nvb_edw` by the upstream bulk loader (`legacy/schedules/runbook.md:3-5`;
golden `manifest.json` lists all 11 under `inputs/D0/`) **[V]**. They land in `bronze` as raw
delivery tables and are published to `silver` with the `nvb_edw` DDL types in section 2 **[I]**.

Schema DDL stays outside unit SQL: prod schemas are Terraform-owned
(`main.tf:87-95`) and dev schemas are created by the bundle/CI per PR, as in DAIWT-03 **[I]**.

## 2. Core table DDL plan (Teradata → Delta)

The per-column mapping for all 20 tables in `legacy/ddl/` (241 columns, every line cited)
is in the appendix (section 7). This section states the rules.

### 2.1 Physical attributes

| Teradata feature | Where | Delta target | Evidence |
|---|---|---|---|
| `CREATE SET TABLE` (silently discards exact-duplicate rows) | 7 dims, `etl_batch_control`, 6 marts | Plain Delta table; duplicate rows are prevented by the unit (dedupe/`MERGE`/grouping), not by the table. Parity is multiset-based, so a duplicate would fail parity rather than be discarded. | e.g. `legacy/ddl/nvb_edw.dim_account.sql:1`, `legacy/ddl/nvb_mart.mart_channel_daily.sql:1`; README "Parity" **[V]** |
| `CREATE MULTISET TABLE` | 4 facts, 2 feeds | Plain Delta table (Delta is multiset by default) | `legacy/ddl/nvb_edw.fct_transaction.sql:1`, `legacy/ddl/nvb_stg.customer_feed.sql:1` **[V]** |
| `FALLBACK` / `NO FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` | every table, lines 2-5 | Dropped (storage redundancy and journaling are platform-managed; Delta log provides history) | e.g. `legacy/ddl/nvb_edw.fct_transaction.sql:2-5` **[V]** |
| `UNIQUE PRIMARY INDEX (k)` | 7 dims | Informational `PRIMARY KEY (k) NOT ENFORCED` (RELY optional) plus `NOT NULL` on `k`; `CLUSTER BY (k)` only if size warrants. Uniqueness itself is enforced by the unit and checked by parity (golden keys are verified unique, `manifest.json` `csv_conventions.outputs.ordering`). | `legacy/ddl/nvb_edw.dim_customer.sql:24` (`UNIQUE PRIMARY INDEX (customer_id)`) **[V]**; constraint choice **[I]** |
| `PRIMARY INDEX (cols)` (non-unique, hash distribution) | 4 facts, `etl_batch_control`, 6 marts | Dropped as a distribution feature. Golden business keys (`manifest.json` `keys`) become `PRIMARY KEY ... NOT ENFORCED` on the 10 contract tables. `fct_transaction` → `CLUSTER BY (txn_date)`; `fct_daily_balance` → `CLUSTER BY (balance_date, account_id)`; small marts unclustered. | `legacy/ddl/nvb_edw.fct_transaction.sql:24`, `legacy/ddl/nvb_edw.fct_daily_balance.sql:21` **[V]**; clustering **[I]** |
| `PARTITION BY RANGE_N(d BETWEEN DATE '2024-01-01' AND DATE '2025-06-30' EACH INTERVAL '1' MONTH)` | `fct_daily_balance`, `fct_transaction` | Liquid clustering on the date column (above), not Hive partitioning (18 monthly partitions over 180,000 / 4,376,000 rows would be small files). The `BETWEEN` range acts as a row-acceptance range in Teradata (rows outside raise an error); the equivalent rule is the explicit window check in the unit (`legacy/bteq/02_transactions.btq:43`). | `legacy/ddl/nvb_edw.fct_daily_balance.sql:22`, `legacy/ddl/nvb_edw.fct_transaction.sql:25`; golden `manifest.json` row counts **[V]**; mapping **[I]** |
| `NO PRIMARY INDEX` | 2 feeds | Dropped | `legacy/ddl/nvb_stg.customer_feed.sql:13`, `legacy/ddl/nvb_stg.transaction_feed.sql:14` **[V]** |
| `COMPRESS (...)` value lists | 16 tables (none on feeds) | Dropped (Delta/Parquet dictionary encoding is automatic). The value lists are **not** domain constraints in Teradata and must not be turned into CHECKs. | e.g. `legacy/ddl/nvb_edw.fct_transaction.sql:15-22` **[V]** |
| `COLLECT STATISTICS` (DDL and in-job, e.g. `legacy/bteq/01_customer.btq:59,74`, `legacy/bteq/02_transactions.btq:66`) | every table | Dropped. Delta collects file-level statistics on write; `ANALYZE TABLE ... COMPUTE STATISTICS` is optional and not needed for parity. | e.g. `legacy/ddl/nvb_edw.fct_transaction.sql:36-40` **[V]**; **[I]** |
| `COMMENT ON TABLE` / `COMMENT ON COLUMN` | every table (4 per edw/mart table, 2 per feed) | Kept verbatim as `COMMENT` on the table and `COMMENT` on the column in the target DDL (governance text, e.g. "Logical delivery timestamp; fixed for repeatable restarts of the same delivery."). | e.g. `legacy/ddl/nvb_edw.fct_transaction.sql:27-34`, `legacy/ddl/nvb_stg.transaction_feed.sql:16-19` **[V]** |
| `DATE FORMAT 'YYYY-MM-DD'` | every `DATE` column | `DATE`; the format clause is display-only and is dropped. Golden dates render `YYYY-MM-DD` (`manifest.json` `csv_conventions.outputs.date`), which is the Statements API rendering of `DATE`. | e.g. `legacy/ddl/nvb_edw.fct_transaction.sql:9` **[V]** |

### 2.2 Column type mapping (applies to all 20 tables)

| Teradata | Delta | Notes | Evidence |
|---|---|---|---|
| `INTEGER` | `INT` | IDs, counts, ranks, `year_no`/`month_no` | e.g. `legacy/ddl/nvb_edw.dim_account.sql:7-9` **[V]** |
| `BIGINT` | `BIGINT` | `transaction_id`, `batch_id`, `row_count`, `reject_count` | `legacy/ddl/nvb_edw.fct_transaction.sql:7,13`, `legacy/ddl/nvb_edw.etl_batch_control.sql:10,20` **[V]** |
| `DECIMAL(18,2)` | `DECIMAL(18,2)` | Money; exact precision/scale kept so the Statements API renders fixed scale (e.g. `0.00`). Verified: `CAST(0 AS DECIMAL(18,2))` renders `0.00`. | e.g. `legacy/ddl/nvb_edw.fct_transaction.sql:10` **[V]** |
| `DECIMAL(6,2)` | `DECIMAL(6,2)` | `dim_product.annual_rate`, `fct_loan.contractual_rate` | `legacy/ddl/nvb_edw.dim_product.sql:9`, `legacy/ddl/nvb_edw.fct_loan.sql:19` **[V]** |
| `CHAR(n) CHARACTER SET LATIN` | `STRING` | All `CHAR` columns are fixed-width codes (`CHAR(1)` flags/status, `CHAR(2)` country, `CHAR(3)` currency) and every value fills the width, so no padding difference arises; golden renders "value as delivered (no Teradata CHAR padding)" (`manifest.json` `csv_conventions.outputs.char`). Do **not** use Delta `CHAR(n)` (it right-pads on read). | e.g. `legacy/ddl/nvb_edw.dim_account.sql:11,15,18` **[V]** |
| `VARCHAR(n) CHARACTER SET LATIN` / `UNICODE` | `STRING` | Delta strings are UTF-8; LATIN vs UNICODE has no target equivalent. Length limits are not enforced by `STRING`; `VARCHAR(n)` Delta type is an option but adds no parity value. | e.g. `legacy/ddl/nvb_edw.dim_customer.sql:8` (`VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC`) **[V]**; **[I]** |
| `NOT CASESPECIFIC` | `STRING` (default binary collation) | Teradata `NOT CASESPECIFIC` makes `=`, `GROUP BY` and `DISTINCT` case-insensitive in Teradata mode. No D0 data depends on it: the customer feed has no name values differing only by case and the transaction feed has 0 channel values that are not already upper-case and trimmed (queries below). Keep binary collation; if a later drop needs it, use `COLLATE UTF8_LCASE` on that column only. | `legacy/ddl/nvb_stg.transaction_feed.sql:7-12`, `legacy/ddl/nvb_edw.dim_customer.sql:8,10` **[V]**; D0 counts **[V]** |
| `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | see 2.1 | **[V]** |
| `TIMESTAMP(0)` | `TIMESTAMP` (session time zone UTC) | see 2.3 | `legacy/ddl/nvb_edw.dim_customer.sql:11-12` **[V]** |

D0 checks behind the `NOT CASESPECIFIC` row (read-only `read_files` over
`inputs/D0/nvb_stg.*`) **[V]**: `customer_feed` 0 rows with leading/trailing blanks in
`customer_name`/`town`, 0 lower-case-equal name variants, 0 non-ASCII characters;
`transaction_feed` 0 rows where `channel <> UPPER(TRIM(channel))` or blank, 0 amounts with
`,`, 0 padded identifiers, 0 values that fail `try_cast`.

### 2.3 `TIMESTAMP(0)` → `TIMESTAMP`, not `TIMESTAMP_NTZ`

Columns: `load_ts` on all 18 `nvb_edw`/`nvb_mart` tables (e.g.
`legacy/ddl/nvb_edw.fct_transaction.sql:14`) and `dim_customer.updated_at`
(`legacy/ddl/nvb_edw.dim_customer.sql:11`) **[V]**.

- The golden renders `TIMESTAMP(0)` as "Statements API TIMESTAMP in UTC:
  `YYYY-MM-DDTHH:MM:SS.000Z`" (`manifest.json` `csv_conventions.outputs.timestamp`) **[V]**.
- Parity reads the target with a plain `SELECT <columns>` through the Statements API, so
  the column's type decides the string (`src/parity/parity_check.py:143-150,205-211`) **[V]**.
- Measured on warehouse `6dc4b80538621b7b` (session `current_timezone()` = `Etc/UTC`):
  `TIMESTAMP'2025-06-30 20:00:00'` → `2025-06-30T20:00:00.000Z`;
  `TIMESTAMP_NTZ'2025-06-30 20:00:00'` → `2025-06-30T20:00:00.000` (no `Z`) **[V]**.
- Decision: `TIMESTAMP` (LTZ) with the job/warehouse session time zone left at UTC. A
  `TIMESTAMP_NTZ` column would fail exact string parity on `dim_customer.updated_at`.
  Feed values are wall-clock strings without offset; parsing them under a UTC session
  stores them unshifted, which is what the golden shows **[V]** (dedupe query in M11
  matched all 5,000 golden rows including `updated_at`).
- `load_ts` is excluded from parity (`manifest.json` `excluded_from_parity`: constant
  `2025-06-30 23:00:00`) **[V]** but uses the same type for consistency **[I]**. The
  schedule time zone is `Europe/Oslo` (`legacy/schedules/nightly.yaml:1`); no unit converts
  time zones, so none is needed **[V]**.

### 2.4 Defaults and CHECK constraints

- **Defaults.** Every `nvb_edw`/`nvb_mart` table carries `DEFAULT`s for the audit columns
  `batch_id 20250630`, `load_ts TIMESTAMP '2025-06-30 23:00:00'`, `src_system_cd 'COREBANK'`,
  `record_status_cd 'A'`, `effective_date DATE '2024-01-01'`, `expiry_date DATE '9999-12-31'`
  plus table-specific code defaults (e.g. `legacy/ddl/nvb_edw.fct_transaction.sql:13-22`);
  the same values are in `data/column_defaults.json` **[V]**. Legacy loads rely on them: the
  macros/procs/BTEQ `INSERT`s list only business columns (e.g.
  `legacy/macros/card_category.sql:11-15`, `legacy/bteq/02_transactions.btq:58-59`), and the
  golden outputs show the defaulted values (e.g. `mart_card_spend_category.csv` has
  `batch_id=20250630`, `src_system_cd=COREBANK`, `record_status_cd=A`,
  `effective_date=2024-01-01`, `expiry_date=9999-12-31`) **[V]**.
  Target: declare `DEFAULT <literal>` on the Delta columns
  (`TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')`) **and** have every
  unit write the audit columns explicitly from one shared projection (M19) so the result
  does not depend on whether a writer path honours column defaults **[I]**.
- **CHECK constraints** (23, all listed in the appendix) **[V]**:
  `record_status_cd IN ('A','D')` on all 18 edw/mart tables; plus
  `dim_account.overdraft_limit >= 0` (`legacy/ddl/nvb_edw.dim_account.sql:21`),
  `dim_customer.consent_marketing_ind IN ('N','Y')` (`legacy/ddl/nvb_edw.dim_customer.sql:22`),
  `dim_product.minimum_balance >= 0` (`legacy/ddl/nvb_edw.dim_product.sql:20`),
  `etl_batch_control.reject_count >= 0` (`legacy/ddl/nvb_edw.etl_batch_control.sql:20`),
  `fct_loan.contractual_rate >= 0` (`legacy/ddl/nvb_edw.fct_loan.sql:19`).
  Target: `ALTER TABLE ... ADD CONSTRAINT <table>_<col>_ck CHECK (...)` (enforced Delta
  CHECK). Teradata CHECK passes `NULL` (unknown is not false) and Delta CHECK behaves the
  same, so nullable columns keep the same acceptance **[I]**.
- **NOT NULL.** Only `batch_id` and `load_ts` are `NOT NULL` in legacy (e.g.
  `legacy/ddl/nvb_edw.dim_account.sql:12-13`) **[V]**; keep exactly those plus the key
  columns used in `PRIMARY KEY ... NOT ENFORCED` **[I]**.

### 2.5 Proposed target DDL shape

One template per table, owned by the unit that writes the table (Wave 2/3), with catalog and
schema bound at run time **[I]**:

```sql
CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_transaction') (
  transaction_id     BIGINT NOT NULL COMMENT 'Business identifier used by nightly reconciliation and consumer joins.',
  account_id         INT,
  txn_date           DATE,
  amount             DECIMAL(18,2),
  channel            STRING,
  merchant_id        INT,
  batch_id           BIGINT NOT NULL DEFAULT 20250630 COMMENT 'Delivery identifier assigned by overnight operations.',
  load_ts            TIMESTAMP NOT NULL DEFAULT TIMESTAMP'2025-06-30 23:00:00' COMMENT 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.',
  src_system_cd      STRING DEFAULT 'COREBANK',
  record_status_cd   STRING DEFAULT 'A',
  effective_date     DATE DEFAULT DATE'2024-01-01',
  expiry_date        DATE DEFAULT DATE'9999-12-31',
  currency_cd        STRING DEFAULT 'NOK',
  posting_status_cd  STRING DEFAULT 'POSTED',
  reversal_ind       STRING DEFAULT 'N',
  clearing_scheme_cd STRING DEFAULT 'DOMESTIC',
  CONSTRAINT fct_transaction_pk PRIMARY KEY (transaction_id) NOT ENFORCED,
  CONSTRAINT fct_transaction_record_status_cd_ck CHECK (record_status_cd IN ('A','D'))
)
CLUSTER BY (txn_date)
COMMENT 'Retail banking ledger and processing records in NOK; retained for control and reporting.'
TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported');
```

Columns, defaults, comment texts and the CHECK come from
`legacy/ddl/nvb_edw.fct_transaction.sql:7-34` **[V]**. Bronze feed tables keep every column
`STRING` (`legacy/ddl/nvb_stg.transaction_feed.sql:7-12`) plus ingestion metadata columns
(`_source_file`, `_ingested_at`) that are not part of any parity contract **[I]**.

### 2.6 Load semantics carried by the DDL/jobs

| Legacy pattern | Tables | Delta equivalent | Evidence |
|---|---|---|---|
| `MERGE` on `customer_id` (update business columns + `batch_id`/`load_ts`, insert new) | `dim_customer` | `MERGE INTO ... USING (deduped source)` with the same column lists | `legacy/bteq/01_customer.btq:62-71` **[V]** |
| Window replace in `BT`/`ET` (`DELETE` window then `INSERT`; `ABORT` on error) | `fct_transaction` | `INSERT INTO ... REPLACE WHERE txn_date BETWEEN ...` (single atomic Delta commit) | `legacy/bteq/02_transactions.btq:53-64,76-78` **[V]**; **[I]** |
| Batch replace in `BT`/`ET` | `mart_product_profitability` | `INSERT OVERWRITE` (one commit) | `legacy/bteq/07_product_profitability.btq:43-53` **[V]** |
| Macro/proc `DELETE` + `INSERT` (no explicit transaction; a macro is one request) | `mart_monthly_balance`, `mart_channel_daily`, `mart_card_spend_category`, `mart_branch_performance`, `mart_customer_segment` | `INSERT OVERWRITE` or `REPLACE WHERE` on the same predicate (M18) | `legacy/macros/*.sql:8-9`, `legacy/procs/branch_league.sql:20`, `legacy/procs/customer_segment.sql:20` **[V]** |
| Two-step `UPDATE` (reset to `N`, then set `Y`) | `dim_account.dormant_flag` | One `MERGE`/`UPDATE` with a `CASE`, or rebuild; must yield N=7,895 / Y=105 on D0 | `legacy/procs/flag_dormant.sql:19-43`; golden `dim_account.csv` counts **[V]** |
| Single-row state `UPDATE`s | `etl_batch_control` | `MERGE` on `(batch_date, job_name)` (M1-M5) | `legacy/bteq/01_customer.btq:8-24` **[V]** |

## 3. Legacy macros → shared SQL

All three macros live in `nvb_edw`, take `(p_from DATE, p_to DATE)` and are one
multi-statement request each (`DELETE` then `INSERT ... SELECT`). Each is called once, by a
BTEQ job, with the full retained window `DATE '2024-01-01', DATE '2025-06-30'` **[V]**.
On Databricks they are **not** recreated as callable objects: each becomes the body of its
Gold unit, written against the shared templates in section 4. Their parameters become SQL
named parameters (`:p_from`, `:p_to`) bound by the unit runner **[I]**.

### 3.1 `nvb_edw.monthly_balance(p_from, p_to)` — `legacy/macros/monthly_balance.sql:1-33`

- Caller: `legacy/bteq/04_monthly_balance.btq:40`; unit `monthly_balance` → `gold.mart_monthly_balance` **[V]**.
- Replace predicate: `month_start BETWEEN TRUNC(:p_from,'MONTH') AND TRUNC(:p_to,'MONTH')` (`:8-9`) **[V]**.
- Body: `fct_daily_balance` `INNER JOIN dim_account` (both `record_status_cd = 'A'`, `:26-31`),
  grouped by account and `TRUNC(balance_date,'MONTH')` (`:20`); average = M14 (`:21`);
  closing = balance on the calendar month-end day, M13 (`:22-25`) **[V]**.
- Pre-condition enforced by the caller: every account-month has every calendar day through
  month-end (`legacy/bteq/04_monthly_balance.btq:34-39`); post-condition: every
  account-month is present with non-null closing balance (`:42-47`) **[V]**.
- Databricks body (verified: 144,000 rows, `EXCEPT ALL` 0/0 both ways against
  `outputs/D0/monthly_balance/mart_monthly_balance.csv`, comparing `account_id`,
  `month_start`, `average_balance`, `closing_balance` as rendered strings) **[V]**:

```sql
SELECT b.account_id,
       trunc(b.balance_date, 'MM')                                   AS month_start,     -- M12
       CAST(AVG(CAST(b.balance AS DECIMAL(28,6))) AS DECIMAL(18,2))  AS average_balance, -- M14
       MAX(CASE WHEN b.balance_date = last_day(b.balance_date)
                THEN b.balance END)                                  AS closing_balance  -- M13
FROM IDENTIFIER(:catalog || '.' || :silver_schema || '.fct_daily_balance') b
JOIN IDENTIFIER(:catalog || '.' || :silver_schema || '.dim_account') a
  ON b.account_id = a.account_id
WHERE b.balance_date BETWEEN :p_from AND :p_to
  AND b.record_status_cd = 'A' AND a.record_status_cd = 'A'
GROUP BY ALL
```

(The verification query read the D0 input CSVs directly; all D0 input rows have
`record_status_cd = 'A'`, which the golden `dim_account.csv` also shows **[V]**.)

### 3.2 `nvb_edw.channel_daily(p_from, p_to)` — `legacy/macros/channel_daily.sql:1-28`

- Caller: `legacy/bteq/08_channel.btq:38`; unit `channel` → `gold.mart_channel_daily` **[V]**.
- Replace predicate: `txn_date BETWEEN :p_from AND :p_to` (`:8-9`) **[V]**.
- Body: `fct_transaction` `INNER JOIN dim_calendar ON txn_date = calendar_date` (`:23-25`),
  `record_status_cd = 'A'` (`:27`); channel = M10 variant `COALESCE(NULLIF(TRIM(channel),''),'ONLINE')`
  (`:20`, no `UPPER` — Silver already upper-cases, `legacy/bteq/02_transactions.btq:33`);
  `COUNT(*)` (`:21`); `ZEROIFNULL(SUM(amount))` = M16 (`:22`) **[V]**.
- Caller guards: every `txn_date` exists in `dim_calendar` (`legacy/bteq/08_channel.btq:33-37`);
  every (date, channel) count/amount equals a direct aggregation of `fct_transaction`
  (`:40-45`) **[V]**.
- Verified: 2,188 rows (547 calendar dates × 4 channels), `EXCEPT ALL` 0/0 against
  `outputs/D0/channel/mart_channel_daily.csv` **[V]**.

### 3.3 `nvb_edw.card_category(p_from, p_to)` — `legacy/macros/card_category.sql:1-30`

- Caller: `legacy/bteq/09_card_category.btq:38`; unit `card_category` → `gold.mart_card_spend_category` **[V]**.
- Replace predicate: `batch_id = 20250630` (`:8-9`) — the whole batch, not the date window **[V]**.
- Body: `fct_transaction` `INNER JOIN dim_merchant` (`:21-23`); `channel = 'CARD' AND amount < 0`
  and both `record_status_cd = 'A'` (`:24-28`); `COALESCE(m.category,'UNCLASSIFIED')` (`:18`);
  `COUNT(t.transaction_id)` (`:19`); spend `SUM(-t.amount)` = M17 (`:20`) **[V]**.
- Verified: 4 rows (FUEL, GROCERY, RETAIL, TRAVEL), `EXCEPT ALL` 0/0 against
  `outputs/D0/card_category/mart_card_spend_category.csv` **[V]**. `UNCLASSIFIED` does not
  occur in D0 (all 80 merchants have a category) **[V]**.

## 4. Shared canonical expression templates (M1..M22)

Each template is the single text every calling unit copies (or calls, where a UDF is
proposed in section 5). Callers use the fixed contract unit names
(`src/parity/contract.json`). Verification status is per template.

### 4.1 Batch-control boilerplate (all ten BTEQ jobs)

Every job opens with the same 24 lines and closes with the same `rejected`/`failed`
labels; only `last_step_cd` changes (e.g. `legacy/bteq/01_customer.btq:1-24,78-87`,
`legacy/bteq/10_close_batch.btq:1-24`) **[V]**. The runbook defines the row as the single
delivery-level state, not a job history (`legacy/schedules/runbook.md:27-30`) **[V]**.

**M1 — Batch lookup + register.** Legacy: `SELECT ... WHERE batch_date = DATE '2025-06-30' AND job_name = 'NIGHTLY'`;
if no row, `INSERT (batch_date, job_name, status, row_count) VALUES (DATE '2025-06-30','NIGHTLY','OPEN',0)`
(`legacy/bteq/01_customer.btq:8-18`) **[V]**. All other columns take their DDL defaults
(`legacy/ddl/nvb_edw.etl_batch_control.sql:11-21`) **[V]**.

```sql
MERGE INTO IDENTIFIER(:catalog || '.' || :silver_schema || '.etl_batch_control') c
USING (SELECT CAST(:batch_date AS DATE) AS batch_date, 'NIGHTLY' AS job_name) s
  ON c.batch_date = s.batch_date AND c.job_name = s.job_name
WHEN NOT MATCHED THEN INSERT (batch_date, job_name, status, row_count, <M19 audit columns>,
       last_step_cd, window_start_date, window_end_date, reject_count, scheduler_group_cd)
VALUES (s.batch_date, s.job_name, 'OPEN', 0, <M19 values>,
       '10_close_batch', DATE'2024-01-01', DATE'2025-06-30', 0, 'NVB_NIGHTLY');
```

(`last_step_cd`/window/`reject_count`/`scheduler_group_cd` values are the DDL defaults,
`legacy/ddl/nvb_edw.etl_batch_control.sql:17-21` **[V]**.)

**M2 — Mark RUNNING, require exactly one row.** Legacy: `UPDATE ... SET status='RUNNING', last_step_cd='<job>'`
then `.IF ACTIVITYCOUNT <> 1 THEN .GOTO rejected` (`legacy/bteq/01_customer.btq:20-24`) **[V]**.
Target: `UPDATE ... WHERE batch_date = :batch_date AND job_name = 'NIGHTLY'` followed by
M4 on `num_affected_rows = 1` (or a `SELECT assert_true(count(*) = 1)` over the row) **[I]**.
`last_step_cd` values are the BTEQ file stems `01_customer` … `10_close_batch`
(e.g. `legacy/bteq/02_transactions.btq:84`) **[V]**.

**M3 — Mark FAILED.** Legacy: on any error or rejection, `UPDATE ... SET status='FAILED', last_step_cd='<job>'`;
exit `12`, or `99` if that update fails (`legacy/bteq/01_customer.btq:80-87`) **[V]**.
Target: a job-level failure handler task (`run_if: AT_LEAST_ONE_FAILED`) running the
`UPDATE`; the job run itself fails (non-zero), which replaces the exit codes **[I]**.

**M4 — Reject probe.** Legacy idiom: `SELECT TOP 1 <offending keys> FROM ... WHERE <violation>`
then `.IF ACTIVITYCOUNT > 0 THEN .GOTO rejected` (`legacy/bteq/01_customer.btq:39-46`;
also `02:38-50`, `03:35-39,42-50`, `04:34-39,42-47`, `05:34-36,39-45`, `06:32-36,39-45`,
`07:39-42`, `08:33-37,40-45`, `09:35-37,40-44`, `10:32-42`) **[V]**. Rejection prints the
offending key into the request log (`legacy/schedules/runbook.md:36`) and publishes nothing
**[V]**. Target (fails the statement, hence the task) **[I]**:

```sql
SELECT raise_error(concat('<unit> control failure: ', CAST(<key> AS STRING)))
FROM (<violation query> LIMIT 1);
```

Procedures use the same idea with `SIGNAL SQLSTATE '45000'` (`legacy/procs/branch_league.sql:14-16,42-46`,
`legacy/procs/customer_segment.sql:14-16,48-52`, `legacy/procs/flag_dormant.sql:15-17,48-51`) and
an `EXIT HANDLER` that `RESIGNAL`s (`legacy/procs/branch_league.sql:6-12`) **[V]**; they map to
the same `raise_error` template.

**M5 — Mark COMPLETE (job 10 only).** `UPDATE ... SET status='COMPLETE', last_step_cd='10_close_batch',
row_count = <payment_count>` after the reconciliation probe, requiring one row
(`legacy/bteq/10_close_batch.btq:43-48`; "Only job 10 may set COMPLETE",
`legacy/schedules/runbook.md:29`) **[V]**. Golden final row: `2025-06-30,NIGHTLY,COMPLETE,180000,...,10_close_batch,2024-01-01,2025-06-30,0,NVB_NIGHTLY`
(`outputs/D0/close_batch/etl_batch_control.csv`) **[V]**. Reconciliation probe: total
`COUNT(*)`/`SUM(amount)` of `fct_transaction` equals `SUM(txn_count)`/`SUM(net_amount)` of
`mart_channel_daily` (`legacy/bteq/10_close_batch.btq:26-42`) **[V]**.

**M6 — Control report export.** `.EXPORT REPORT FILE = control_reports/02_transactions.txt`
(count, amount, min/max date; `legacy/bteq/02_transactions.btq:68-73`) and
`control_reports/nightly_release.txt` (release-state row; `legacy/bteq/10_close_batch.btq:51-55`);
retained 400 days with the manifest and Finance Control approval
(`legacy/schedules/runbook.md:22-25`, `legacy/schedules/nightly.yaml:10`) **[V]**. The other
jobs print summary `SELECT`s to the request log only (e.g. `legacy/bteq/04_monthly_balance.btq:50-51`,
`legacy/bteq/08_channel.btq:48-50`) **[V]**. Target: write the same result set as a file to
the control-reports volume (section 6) with `INSERT OVERWRITE DIRECTORY` or a Python task,
path `.../control_reports/<batch_date>/<name>.txt` **[I]**. Not part of parity **[V]**
(not in `src/parity/contract.json`).

### 4.2 Cleansing (Silver: `customer`, `transactions`)

**M7 — Trim + safe cast.** `TRYCAST(TRIM(x) AS INTEGER|BIGINT|DATE|TIMESTAMP(0))`
(`legacy/bteq/01_customer.btq:29,31,33`, `legacy/bteq/02_transactions.btq:29-31,34`) **[V]**
→ `try_cast(trim(x) AS INT|BIGINT|DATE|TIMESTAMP)`. Measured: `try_cast(trim(' 12 ') AS INT)` = 12,
`try_cast('x' AS INT)` = `NULL` **[V]**. Callers: `customer`, `transactions`.

**M8 — Non-breaking-space clean.** `TRIM(OREPLACE(x, U+00A0, ' '))` — the second argument is the
byte sequence `C2 A0` (verified with `od -c` on `legacy/bteq/01_customer.btq:30`) **[V]**
(`:30,32`) → `trim(replace(x, char(160), ' '))` **[V]** (used in the M11 verification). Caller: `customer`.

**M9 — Decimal-comma amount.** `TRYCAST(OREPLACE(TRIM(amount), ',', '.') AS DECIMAL(18,2))`
(`legacy/bteq/02_transactions.btq:32`) → `try_cast(replace(trim(amount), ',', '.') AS DECIMAL(18,2))` **[V]**.
Both engines round a cast to scale 2 half away from zero: measured
`CAST(CAST(-2.345 AS DECIMAL(28,6)) AS DECIMAL(18,2))` = `-2.35` on Databricks **[V]**; the
Teradata behaviour is **[I]** (matches the golden's `ROUND_HALF_UP`, `manifest.json`
`csv_conventions.outputs.decimal`). Caller: `transactions`.

**M10 — Channel normalisation.** Silver: `COALESCE(NULLIF(UPPER(TRIM(channel)), ''), 'ONLINE')`
(`legacy/bteq/02_transactions.btq:33`); Gold macro variant without `UPPER`
(`legacy/macros/channel_daily.sql:20`); Python reference
`str.strip().str.upper().replace("", "ONLINE").fillna("ONLINE")` (`golden/compute_baselines.py`,
`clean_payments`) **[V]**. Allowed domain after normalisation: `CARD`, `MOBILE`, `BRANCH`,
`ONLINE` (`legacy/bteq/02_transactions.btq:45`) **[V]**. Target:
`coalesce(nullif(upper(trim(channel)), ''), 'ONLINE')`; the Gold unit may reuse the same
expression (idempotent on already-normalised values) **[I]**. Callers: `transactions`, `channel`.
Verified with M7/M9: `fct_transaction` 180,000 rows `EXCEPT ALL` 0/0 against
`outputs/D0/transactions/fct_transaction.csv` **[V]**.

**M11 — Latest-record dedupe.** `QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id
ORDER BY updated_at DESC, customer_name DESC, town DESC, branch_id DESC) = 1`
(`legacy/bteq/01_customer.btq:52-56`) → identical Databricks `QUALIFY` **[V]**. Verified with
M7/M8: 5,100 feed rows → 5,000, `EXCEPT ALL` 0/0 on `customer_id, customer_name, branch_id,
town, updated_at` against `outputs/D0/customer/dim_customer.csv` **[V]**. Caller: `customer`.
(`HASHROW(...) AS source_hash`, `legacy/bteq/01_customer.btq:34`, is computed but not
selected into `vt_customer`, `:50`, so it has no target **[V]**.)

Duplicate-key probe for transactions: `GROUP BY 1 HAVING COUNT(*) > 1`
(`legacy/bteq/02_transactions.btq:48-50`) → M4 with the same query **[V]**.

### 4.3 Dates and windows

**M12 — Month start.** `TRUNC(d, 'MONTH')` (`legacy/macros/monthly_balance.sql:9,20`,
`legacy/bteq/04_monthly_balance.btq:28`) → `trunc(d, 'MM')` (returns `DATE`; measured
`trunc(DATE'2024-02-10','MM')` = `2024-02-01`) **[V]**. Do not use `date_trunc`, which
returns `TIMESTAMP` **[I]**. Callers: `monthly_balance`.

**M13 — Month end.** `ADD_MONTHS(TRUNC(d,'MONTH'), 1) - 1` (`legacy/macros/monthly_balance.sql:23`,
`legacy/bteq/04_monthly_balance.btq:36`) → `last_day(d)` (measured `last_day(DATE'2024-02-10')`
= `2024-02-29` = `date_add(add_months(trunc(d,'MM'),1),-1)`) **[V]**. Completeness rule
`ledger_days = EXTRACT(DAY FROM latest_date)` (`legacy/bteq/04_monthly_balance.btq:37`) →
`count(*) = day(max(balance_date))` **[I]**. Callers: `monthly_balance`.

**M14 — Money mean.** `CAST(AVG(CAST(x AS DECIMAL(28,6))) AS DECIMAL(18,2))`
(`legacy/macros/monthly_balance.sql:21`; `legacy/bteq/07_product_profitability.btq:29,49`) → same
text **[V]**. Databricks `AVG(DECIMAL(28,6))` returns `DECIMAL(32,10)` (measured), so the
final cast rounds a 10-digit-scale mean **[V]**. Product income keeps the **unrounded**
mean: `CAST(precise_balance * annual_rate / 100 AS DECIMAL(18,2))`
(`legacy/bteq/07_product_profitability.btq:50`). Verified: the 4 products match
`mart_product_profitability.csv` exactly; computing income from the already-rounded
average instead gives `20876.39` for product 3 versus golden `20876.38` **[V]**.
Callers: `monthly_balance`, `product_profitability`.

**M15 — `ADD_MONTHS` / day-offset windows** (as-of date `2025-06-30`). Databricks `add_months`
matches Teradata `ADD_MONTHS` here (measured `add_months(DATE'2025-06-30',-6)` =
`2024-12-30`; no month-end snapping) **[V]**; the runbook states the resulting dates
(`legacy/schedules/runbook.md:48-51`) **[V]**.

| Id | Legacy | Databricks | Resolved D0 bounds | Callers |
|---|---|---|---|---|
| M15a dormancy | `txn_date > ADD_MONTHS(as_of_date, -6) AND txn_date <= as_of_date` (`legacy/procs/flag_dormant.sql:40-41`); verify `<= ADD_MONTHS(DATE '2025-06-30', -6)` (`legacy/bteq/03_dormant.btq:47`) | `txn_date > add_months(:as_of, -6) AND txn_date <= :as_of` | 2024-12-31 .. 2025-06-30 (matches `golden/compute_baselines.py` `between("2024-12-31","2025-06-30")`) **[V]** | `dormant` |
| M15b trailing year | `t.txn_date BETWEEN ADD_MONTHS(as_of_date, -12) + 1 AND as_of_date` (`legacy/procs/customer_segment.sql:44`) | `BETWEEN date_add(add_months(:as_of, -12), 1) AND :as_of` | 2024-07-01 .. 2025-06-30 (measured) **[V]** | `customer_segment` |
| M15c staleness | `MAX(t.txn_date) < as_of_date - 90` (`legacy/procs/customer_segment.sql:31`) | `max(t.txn_date) < date_sub(:as_of, 90)` | `< 2025-04-01` (measured) **[V]** | `customer_segment` |
| M15d fixed windows | `BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'` (`legacy/bteq/02_transactions.btq:43,56`; `legacy/bteq/07_product_profitability.btq:35`; `legacy/procs/branch_league.sql:38`; `legacy/views/v_channel_trend.sql:21`; macro calls `04:40`, `08:38`, `09:38`) | `BETWEEN :window_start AND :window_end` bound from `etl_batch_control.window_start_date/window_end_date` (defaults `2024-01-01`/`2025-06-30`, `legacy/ddl/nvb_edw.etl_batch_control.sql:18-19`) | as legacy **[V]** | `transactions`, `product_profitability`, `branch_league`, `monthly_balance`, `channel`, `card_category` |

The as-of/business date is `2025-06-30` everywhere (`legacy/schedules/nightly.yaml:2`;
literal in every BTEQ batch-control statement, e.g. `legacy/bteq/01_customer.btq:10`) **[V]**;
units take it as one parameter `:batch_date` **[I]**.

### 4.4 Null/zero handling and aggregates

**M16 — `ZEROIFNULL` / `NULLIFZERO`.** `ZEROIFNULL(x)` → `coalesce(x, 0)`; `NULLIFZERO(x)` →
`nullif(x, 0)` **[V]** (semantics are definitional). Uses: `legacy/macros/channel_daily.sql:22`,
`legacy/procs/branch_league.sql:31-32`, `legacy/procs/customer_segment.sql:28`,
`legacy/views/v_channel_trend.sql:8-12`, `legacy/views/v_customer_portfolio.sql:12`,
`legacy/views/v_credit_risk.sql:10`, `legacy/bteq/08_channel.btq:49`,
`legacy/bteq/09_card_category.btq:48`, `reports/card_services.sql:2` **[V]**. Division result
type differs: Databricks `DECIMAL(18,2) / DECIMAL(18,2)` returns `DECIMAL(38,20)` (measured
`1.00/3.00` → `0.33333333333333333333`) **[V]**; Teradata's result scale is **[I]**. Only
views/reports divide, and none of them is a parity target, so the Wave 3 views should state
their output scale explicitly (e.g. `CAST(... AS DECIMAL(18,4))`) once reconciled to
`reports/*.sql` **[I]**. `branch_league` rank: `RANK() OVER (ORDER BY ZEROIFNULL(SUM(t.amount)) DESC)`
(`legacy/procs/branch_league.sql:32`) → `rank() OVER (ORDER BY coalesce(sum(t.amount),0) DESC)`;
D0 has no ties (8 distinct `net_amount`, ranks 1-8 in `mart_branch_performance.csv`) **[V]**.

**M17 — Debit spend (positive).** Two forms: filter-then-negate `SUM(-t.amount)` with
`t.amount < 0` (`legacy/macros/card_category.sql:20,25`) and conditional
`ZEROIFNULL(SUM(CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END))`
(`legacy/procs/customer_segment.sql:28,34`) **[V]**. Canonical target:
`coalesce(sum(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 0)` where rows are not
pre-filtered; keep `SUM(-amount)` in `card_category` where they are **[I]**. Callers:
`card_category`, `customer_segment`; view `v_customer_portfolio` reads it as
`trailing_year_debit_spend` (`legacy/views/v_customer_portfolio.sql:12`) **[V]**.

**M18 — Replace-by-predicate.** `DELETE <predicate>; INSERT ... SELECT` (macros `:8-9`,
`legacy/procs/branch_league.sql:20`, `legacy/procs/customer_segment.sql:20`,
`legacy/bteq/02_transactions.btq:55-56`, `legacy/bteq/07_product_profitability.btq:45`) **[V]**
→ `INSERT INTO <t> REPLACE WHERE <predicate> SELECT ...` (date windows) or
`INSERT OVERWRITE <t> SELECT ...` (`batch_id = 20250630` replaces the whole single-batch
table) **[I]**. Both are one atomic Delta commit, which also satisfies "treat the mart as
unavailable until its rebuild succeeds" (`legacy/schedules/runbook.md:15-16,44-46`) without
the window of a half-rebuilt table **[I]**.

**M19 — Audit/default columns projection.** `batch_id`, `load_ts`, `src_system_cd`,
`record_status_cd`, `effective_date`, `expiry_date` with the literal defaults in section 2.4
(e.g. `legacy/ddl/nvb_mart.mart_channel_daily.sql:11-16`; `data/column_defaults.json`) **[V]**.
Target snippet (inline, appended to every Silver/Gold `SELECT`) **[I]**:

```sql
CAST(20250630 AS BIGINT) AS batch_id, TIMESTAMP'2025-06-30 23:00:00' AS load_ts,
'COREBANK' AS src_system_cd, 'A' AS record_status_cd,
DATE'2024-01-01' AS effective_date, DATE'9999-12-31' AS expiry_date
```

Table-specific code defaults (e.g. `currency_cd 'NOK'`, `posting_status_cd 'POSTED'`,
`legacy/ddl/nvb_edw.fct_transaction.sql:19-22`) are added per table from the appendix.
`dim_customer` `MERGE` sets `batch_id`/`load_ts` on update as literals
(`legacy/bteq/01_customer.btq:67`) **[V]**.

**M20 — Running total and moving average (views).** `CSUM(x, d)` and `MAVG(x, 7, d)`
(`legacy/views/v_channel_trend.sql:10-11`) → `sum(x) OVER (ORDER BY d ROWS BETWEEN UNBOUNDED
PRECEDING AND CURRENT ROW)` and `avg(x) OVER (ORDER BY d ROWS BETWEEN 6 PRECEDING AND
CURRENT ROW)` **[I]** (Teradata `MAVG` averages the available rows for the first n-1 rows,
which the `ROWS` frame reproduces; not verifiable here — no live Teradata, `PROVENANCE.json`).
The view's `x` is `ZEROIFNULL(m.net_amount)` over a calendar `LEFT JOIN`, so days with no
activity contribute 0 (`legacy/views/v_channel_trend.sql:9,17-20`) **[V]**. Caller: Wave 3
business view for `reports/*` consumers.

**M21 — Share of total.** `x / NULLIFZERO(SUM(x) OVER ())` (`legacy/bteq/09_card_category.btq:48`,
`reports/card_services.sql:2`) → `x / nullif(sum(x) OVER (), 0)` **[V]**. Caller: Wave 3
card-services view.

**M22 — Dynamic object naming.** Every unit references objects as
`IDENTIFIER(:catalog || '.' || :<layer>_schema || '.<table>')`, never a literal catalog or
legacy database name; the parity harness resolves `<layer>` (prod) or `<layer>_<suffix>`
(dev) the same way (`src/parity/parity_check.py:205-211`) **[V]**; pattern from DAIWT-03 **[I]**.

## 5. Candidate shared SQL functions (`sql_macros` unit)

The estate has no Teradata UDFs (`legacy/` has `bteq`, `ddl`, `macros`, `procs`, `views`,
`schedules` only) **[V]**, so the `sql_macros` unit is mostly the reference catalogue of
section 4. Recommended small set of UC SQL functions, created by `sql_macros` in the Silver
schema of the target (`silver` / `silver_pr_<n>`) so dev PRs stay isolated **[I]**:

| Function | Signature | Body | Template | Calling units |
|---|---|---|---|---|
| `nvb_clean_text` | `(s STRING) RETURNS STRING` | `trim(replace(s, char(160), ' '))` | M8 | `customer` |
| `nvb_parse_amount` | `(s STRING) RETURNS DECIMAL(18,2)` | `try_cast(replace(trim(s), ',', '.') AS DECIMAL(18,2))` | M9 | `transactions` |
| `nvb_norm_channel` | `(s STRING) RETURNS STRING` | `coalesce(nullif(upper(trim(s)), ''), 'ONLINE')` | M10 | `transactions`, `channel` |
| `nvb_month_start` | `(d DATE) RETURNS DATE` | `trunc(d, 'MM')` | M12 | `monthly_balance` |
| `nvb_month_end` | `(d DATE) RETURNS DATE` | `last_day(d)` | M13 | `monthly_balance` |

```sql
CREATE OR REPLACE FUNCTION IDENTIFIER(:catalog || '.' || :silver_schema || '.nvb_norm_channel')(s STRING)
RETURNS STRING
DETERMINISTIC
COMMENT 'M10: channel normalisation; blank or NULL -> ONLINE'
RETURN coalesce(nullif(upper(trim(s)), ''), 'ONLINE');
```

Everything else (M1-M7, M11, M14-M22) stays an inline template: they are statements or
aggregates/window expressions, and a SQL UDF cannot wrap an aggregate **[I]**. Trade-off,
as in DAIWT-03: a function change has an all-callers blast radius, so `sql_macros` is a
dependency of every unit that calls one, and units that only inline templates do not depend
on it (`docs/registry.md` dependency semantics) **[I]**. Inline alternative: the five
functions are one-liners; inlining them removes the dependency at the cost of repeating
the text **[I]**.

### 5.1 Calling-unit matrix

| Unit (contract name) | Templates |
|---|---|
| `customer` | M1, M2, M3, M4, M7, M8, M11, M19 |
| `transactions` | M1-M4, M6, M7, M9, M10, M15d, M18, M19 |
| `dormant` | M1-M4, M15a, M19 |
| `monthly_balance` | M1-M4, M12, M13, M14, M15d, M18, M19 |
| `customer_segment` | M1-M4, M15b, M15c, M16, M17, M18, M19 |
| `branch_league` | M1-M4, M15d, M16, M18, M19 |
| `product_profitability` | M1-M4, M14, M15d, M18, M19 |
| `channel` | M1-M4, M10, M15d, M16, M18, M19 |
| `card_category` | M1-M4, M15d, M17, M18, M19 |
| `close_batch` | M1-M5, M6, M19 |
| Wave 3 business views | M16, M20, M21, M22 |

(Every BTEQ job runs M1-M4; derived from `legacy/bteq/0*.btq:1-24` and each job's
`rejected`/`failed` block **[V]**; the per-unit expression assignment follows the source
lines cited in section 4 **[V]**.)

## 6. Volumes and other UC objects the bundle needs (prod)

Terraform today (`daiwt-01-demo/platform/databricks/main.tf`) **[V]**: catalogs
`daiwt_01_mig_dev`, `daiwt_01_mig_prod` (`:27-40`); schemas dev `bronze`/`silver`/`gold` and
prod `bronze`/`silver`/`gold`/`landing` (`:7-13,87-95`); managed volumes
`daiwt_01_mig_prod.landing.golden` and `daiwt_01_mig_prod.landing.bronze_landing`
(`:114-122`). CI has `ALL_PRIVILEGES` on prod `bronze`/`silver`/`gold` (`:97-104`), only
`USE_SCHEMA` on `landing` (`:128-132`), `READ_VOLUME`/`WRITE_VOLUME` on `bronze_landing`
(`:134-138`) and `READ_VOLUME` on `golden` (`:140-144`) **[V]**. CI therefore cannot create
volumes or tables in `landing` **[V]** (grants above); anything new there needs Terraform.

| Object | Purpose / legacy evidence | Declared in Terraform? | Owner proposal |
|---|---|---|---|
| `daiwt_01_mig_prod.landing.bronze_landing` (volume) | Drop zone for the 11 deliveries, one folder per feed (`<feed>/<batch_date>/`); "Replace upstream tables using the approved bulk loader; retain manifest counts" (`legacy/schedules/runbook.md:4-5`), scheduler step `upstream` (`legacy/schedules/nightly.yaml:12`) | **Yes** (`main.tf:114-122`) | Terraform (exists) |
| `daiwt_01_mig_prod.landing.golden` (volume) | Parity baselines, read-only | **Yes** (`main.tf:114-122`) | Terraform (exists) |
| 11 bronze tables `bronze.customer_feed`, `bronze.transaction_feed`, `bronze.dim_account`, `bronze.dim_branch`, `bronze.dim_calendar`, `bronze.dim_card`, `bronze.dim_merchant`, `bronze.dim_product`, `bronze.fct_daily_balance`, `bronze.fct_loan`, `bronze.fct_product_order` | Raw deliveries (golden `inputs/D0/`, 11 feeds) | No — **NOT IN TERRAFORM** (bundle-owned tables; CI can create them in `bronze`) | `bronze_landing` unit |
| 12 silver + 6 gold tables (section 7) | Target of every `nvb_edw`/`nvb_mart` DDL | No — **NOT IN TERRAFORM** (bundle-owned) | Wave 2/3 units |
| **Control-reports volume**, proposed `daiwt_01_mig_prod.landing.control_reports` | "protected control_reports directory" created by the scheduler before BTEQ; job 02 and job 10 export files; retain 400 days with manifest and Finance Control approval (`legacy/schedules/runbook.md:22-25`; `legacy/schedules/nightly.yaml:8-10`; `legacy/bteq/02_transactions.btq:68`, `legacy/bteq/10_close_batch.btq:51`) | No — **NOT IN TERRAFORM; FLAG.** Must not be bundle-owned if it has to survive dev/prod cleanup and be "protected" (README: Terraform-owned landing resources are not cleanup targets). CI needs `READ_VOLUME`/`WRITE_VOLUME` on it. | Terraform (new volume + grant) |
| **Request-log retention** (90 days) | `retention_days.request_logs: 90` (`legacy/schedules/nightly.yaml:9`); stdout/ERROROUT captured (`legacy/schedules/runbook.md:23`) | No — **NOT IN TERRAFORM; FLAG.** Job run output covers it if workspace run retention is acceptable; otherwise a `request_logs/` folder in the control-reports volume | Decision for orchestration unit |
| **Checkpoint / schema-tracking location** for Auto Loader (if bronze ingestion is streaming `cloud_files`) | Incremental file discovery over `bronze_landing` **[I]** | No — **NOT IN TERRAFORM; FLAG.** Not needed if bronze uses Lakeflow SDP (pipeline-managed checkpoints) or batch `read_files` + `INSERT OVERWRITE` per batch. If needed: managed volume `daiwt_01_mig_prod.bronze._checkpoints` (CI can create it under its `bronze` `ALL_PRIVILEGES`), dev equivalent per `bronze_pr_<n>` | `bronze_landing` unit |
| **UC SQL functions** `nvb_clean_text`, `nvb_parse_amount`, `nvb_norm_channel`, `nvb_month_start`, `nvb_month_end` in `silver` | Section 5 | No — **NOT IN TERRAFORM** (bundle-owned; CI can create in `silver`) | `sql_macros` unit |
| **Business views** in `gold` (`v_channel_trend`, `v_credit_risk`, `v_customer_portfolio`) | `legacy/views/*.sql:1`; consumers `reports/*.sql` | No — **NOT IN TERRAFORM** (bundle-owned) | Wave 3 |
| **Delivery lock / single run** | `concurrency: 1`, `lock_resource: nvb_nightly_exclusive`; "Do not run two copies concurrently" (`legacy/schedules/nightly.yaml:4,7`; `legacy/schedules/runbook.md:14-15,30`) | Not a UC object; job setting `max_concurrent_runs: 1` in the bundle **[I]** | orchestration unit |
| **Prior reporting snapshot** | "Preserve the prior reporting snapshot until reconciliation is approved" (`legacy/schedules/runbook.md:17-18,44-45`) | Not in Terraform. Delta time travel covers it if consumers read a pinned version or a publish step swaps views after M5 **[I]**; a separate `publish`/`reporting` schema would be **NOT IN TERRAFORM; FLAG** | orchestration/governance decision |
| Prod dependency on `golden` for parity only | `databricks.yml` `golden_path`; `src/parity/parity_check.py` `GOLDEN_PATH` | Yes | — |

Retention: `control_reports` 400 days (`legacy/schedules/nightly.yaml:10`) has no UC-native
setting on a volume **[I]**; it needs a housekeeping task or an agreed manual policy.

## 7. Appendix — DDL mapping for every file in `legacy/ddl/`

Generated by reading each file line by line (all 20 files, 241 columns) **[V]**. Columns:
the legacy line; Teradata type with `CHARACTER SET`, `NOT CASESPECIFIC`, `FORMAT` and
`NOT NULL`; the Delta type (section 2.2); the `DEFAULT` to declare (section 2.4); the
`CHECK` to add as a named Delta constraint; and the `COMPRESS` list, which is dropped.
Common rules (sections 2.1-2.4): table options on lines 2-5 dropped; `PRIMARY INDEX`/
`UNIQUE PRIMARY INDEX` → `PRIMARY KEY ... NOT ENFORCED` on the business key + optional
`CLUSTER BY`; `RANGE_N` → `CLUSTER BY` on the date; `COMMENT ON` kept; `COLLECT STATISTICS`
dropped.

Target table names: `nvb_stg.*` → `bronze.*`, `nvb_edw.*` → `silver.*`, `nvb_mart.*` → `gold.*`.
Parity keys for the 10 contract tables come from golden `manifest.json` (`keys`):
`dim_customer(customer_id)`, `fct_transaction(transaction_id)`, `dim_account(account_id)`,
`etl_batch_control(batch_date, job_name)`, `mart_monthly_balance(account_id, month_start)`,
`mart_customer_segment(customer_id)`, `mart_branch_performance(branch_id)`,
`mart_product_profitability(product_id)`, `mart_channel_daily(txn_date, channel)`,
`mart_card_spend_category(category)` **[V]**. Where these differ from the legacy
`PRIMARY INDEX` (`etl_batch_control` PI `batch_date`, `mart_monthly_balance` PI `account_id`,
`mart_channel_daily` PI `txn_date`), the Delta `PRIMARY KEY` uses the golden key **[I]**.

### `nvb_edw.dim_account` → `silver.dim_account` (`legacy/ddl/nvb_edw.dim_account.sql:1-38`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_edw.dim_account.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.dim_account.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `UNIQUE PRIMARY INDEX (account_id)` (`legacy/ddl/nvb_edw.dim_account.sql:23`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `account_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `customer_id` | `INTEGER` | `INT` |  |  |  |
| 9 | `product_id` | `INTEGER` | `INT` |  |  |  |
| 10 | `opened_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` |  |  |  |
| 11 | `dormant_flag` | `CHAR(1) CHARACTER SET LATIN` | `STRING` |  |  | `('N','Y')` |
| 12 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 13 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 14 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 15 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 16 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 17 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 18 | `currency_cd` | `CHAR(3) CHARACTER SET LATIN` | `STRING` | `'NOK'` |  | `('NOK')` |
| 19 | `account_status_cd` | `VARCHAR(10) CHARACTER SET LATIN` | `STRING` | `'OPEN'` |  | `('OPEN','BLOCKED','CLOSED')` |
| 20 | `accrual_basis_cd` | `VARCHAR(8) CHARACTER SET LATIN` | `STRING` | `'ACT365'` |  | `('ACT365','ACT360')` |
| 21 | `overdraft_limit` | `DECIMAL(18,2)` | `DECIMAL(18,2)` | `0.00` | `CHECK (overdraft_limit >= 0)` |  |

- `COMMENT ON TABLE nvb_edw.dim_account` (`legacy/ddl/nvb_edw.dim_account.sql:25-26`): 'Current retail banking reference attributes; corrections overwrite the current record.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_account.account_id` (`legacy/ddl/nvb_edw.dim_account.sql:27-28`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_account.batch_id` (`legacy/ddl/nvb_edw.dim_account.sql:29-30`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_account.load_ts` (`legacy/ddl/nvb_edw.dim_account.sql:31-32`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.dim_account.sql:34-38`): (account_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.dim_branch` → `silver.dim_branch` (`legacy/ddl/nvb_edw.dim_branch.sql:1-36`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_edw.dim_branch.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.dim_branch.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `UNIQUE PRIMARY INDEX (branch_id)` (`legacy/ddl/nvb_edw.dim_branch.sql:21`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `branch_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `town` | `VARCHAR(30) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 9 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 10 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 11 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 12 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 13 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 14 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 15 | `branch_name` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` | `'Nordvik Lokalbank'` |  |  |
| 16 | `region_cd` | `VARCHAR(8) CHARACTER SET LATIN` | `STRING` | `'NORGE'` |  | `('NORGE')` |
| 17 | `branch_type_cd` | `VARCHAR(10) CHARACTER SET LATIN` | `STRING` | `'RETAIL'` |  | `('RETAIL','ADVISORY')` |
| 18 | `service_language_cd` | `CHAR(2) CHARACTER SET LATIN` | `STRING` | `'nb'` |  | `('nb','nn')` |
| 19 | `branch_status_cd` | `VARCHAR(10) CHARACTER SET LATIN` | `STRING` | `'OPEN'` |  | `('OPEN','CLOSED')` |

- `COMMENT ON TABLE nvb_edw.dim_branch` (`legacy/ddl/nvb_edw.dim_branch.sql:23-24`): 'Current retail banking reference attributes; corrections overwrite the current record.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_branch.branch_id` (`legacy/ddl/nvb_edw.dim_branch.sql:25-26`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_branch.batch_id` (`legacy/ddl/nvb_edw.dim_branch.sql:27-28`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_branch.load_ts` (`legacy/ddl/nvb_edw.dim_branch.sql:29-30`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.dim_branch.sql:32-36`): (branch_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.dim_calendar` → `silver.dim_calendar` (`legacy/ddl/nvb_edw.dim_calendar.sql:1-35`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_edw.dim_calendar.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.dim_calendar.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `UNIQUE PRIMARY INDEX (calendar_date)` (`legacy/ddl/nvb_edw.dim_calendar.sql:20`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `calendar_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` |  |  |  |
| 8 | `month_start` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` |  |  |  |
| 9 | `year_no` | `INTEGER` | `INT` |  |  |  |
| 10 | `month_no` | `INTEGER` | `INT` |  |  |  |
| 11 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 12 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 13 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 14 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 15 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 16 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 17 | `calendar_name` | `VARCHAR(20) CHARACTER SET LATIN` | `STRING` | `'NORWEGIAN_RETAIL'` |  |  |
| 18 | `business_day_rule_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'ALL_DAYS'` |  |  |

- `COMMENT ON TABLE nvb_edw.dim_calendar` (`legacy/ddl/nvb_edw.dim_calendar.sql:22-23`): 'Current retail banking reference attributes; corrections overwrite the current record.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_calendar.calendar_date` (`legacy/ddl/nvb_edw.dim_calendar.sql:24-25`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_calendar.batch_id` (`legacy/ddl/nvb_edw.dim_calendar.sql:26-27`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_calendar.load_ts` (`legacy/ddl/nvb_edw.dim_calendar.sql:28-29`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.dim_calendar.sql:31-35`): (calendar_date), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.dim_card` → `silver.dim_card` (`legacy/ddl/nvb_edw.dim_card.sql:1-37`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_edw.dim_card.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.dim_card.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `UNIQUE PRIMARY INDEX (card_id)` (`legacy/ddl/nvb_edw.dim_card.sql:22`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `card_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `account_id` | `INTEGER` | `INT` |  |  |  |
| 9 | `card_type` | `VARCHAR(10) CHARACTER SET UNICODE` | `STRING` |  |  | `('DEBIT','CREDIT')` |
| 10 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 11 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 12 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 13 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 14 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 15 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 16 | `network_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'BANKAXEPT'` |  | `('BANKAXEPT','VISA')` |
| 17 | `card_status_cd` | `VARCHAR(10) CHARACTER SET LATIN` | `STRING` | `'ACTIVE'` |  | `('ACTIVE','BLOCKED','EXPIRED')` |
| 18 | `card_expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2028-01-31'` |  |  |
| 19 | `contactless_ind` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'Y'` |  | `('N','Y')` |
| 20 | `online_enabled_ind` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'Y'` |  | `('N','Y')` |

- `COMMENT ON TABLE nvb_edw.dim_card` (`legacy/ddl/nvb_edw.dim_card.sql:24-25`): 'Current retail banking reference attributes; corrections overwrite the current record.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_card.card_id` (`legacy/ddl/nvb_edw.dim_card.sql:26-27`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_card.batch_id` (`legacy/ddl/nvb_edw.dim_card.sql:28-29`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_card.load_ts` (`legacy/ddl/nvb_edw.dim_card.sql:30-31`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.dim_card.sql:33-37`): (card_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.dim_customer` → `silver.dim_customer` (`legacy/ddl/nvb_edw.dim_customer.sql:1-39`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_edw.dim_customer.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.dim_customer.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `UNIQUE PRIMARY INDEX (customer_id)` (`legacy/ddl/nvb_edw.dim_customer.sql:24`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `customer_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `customer_name` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 9 | `branch_id` | `INTEGER` | `INT` |  |  |  |
| 10 | `town` | `VARCHAR(30) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 11 | `updated_at` | `TIMESTAMP(0)` | `TIMESTAMP` |  |  |  |
| 12 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 13 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 14 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 15 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 16 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 17 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 18 | `customer_type_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'PERSONAL'` |  | `('PERSONAL','BUSINESS')` |
| 19 | `residence_country_cd` | `CHAR(2) CHARACTER SET LATIN` | `STRING` | `'NO'` |  | `('NO','SE','DK')` |
| 20 | `preferred_language_cd` | `CHAR(2) CHARACTER SET LATIN` | `STRING` | `'nb'` |  | `('nb','nn','en')` |
| 21 | `kyc_status_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'VERIFIED'` |  | `('VERIFIED','REVIEW')` |
| 22 | `consent_marketing_ind` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'N'` | `CHECK (consent_marketing_ind IN ('N','Y'))` | `('N','Y')` |

- `COMMENT ON TABLE nvb_edw.dim_customer` (`legacy/ddl/nvb_edw.dim_customer.sql:26-27`): 'Current retail banking reference attributes; corrections overwrite the current record.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_customer.customer_id` (`legacy/ddl/nvb_edw.dim_customer.sql:28-29`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_customer.batch_id` (`legacy/ddl/nvb_edw.dim_customer.sql:30-31`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_customer.load_ts` (`legacy/ddl/nvb_edw.dim_customer.sql:32-33`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.dim_customer.sql:35-39`): (customer_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.dim_merchant` → `silver.dim_merchant` (`legacy/ddl/nvb_edw.dim_merchant.sql:1-36`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_edw.dim_merchant.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.dim_merchant.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `UNIQUE PRIMARY INDEX (merchant_id)` (`legacy/ddl/nvb_edw.dim_merchant.sql:21`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `merchant_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `merchant_name` | `VARCHAR(40) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 9 | `category` | `VARCHAR(15) CHARACTER SET UNICODE` | `STRING` |  |  |  |
| 10 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 11 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 12 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 13 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 14 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 15 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 16 | `country_cd` | `CHAR(2) CHARACTER SET LATIN` | `STRING` | `'NO'` |  | `('NO','SE','DK')` |
| 17 | `settlement_currency_cd` | `CHAR(3) CHARACTER SET LATIN` | `STRING` | `'NOK'` |  | `('NOK')` |
| 18 | `acquirer_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'NVB'` |  | `('NVB')` |
| 19 | `merchant_status_cd` | `VARCHAR(10) CHARACTER SET LATIN` | `STRING` | `'ACTIVE'` |  | `('ACTIVE','BLOCKED')` |

- `COMMENT ON TABLE nvb_edw.dim_merchant` (`legacy/ddl/nvb_edw.dim_merchant.sql:23-24`): 'Current retail banking reference attributes; corrections overwrite the current record.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_merchant.merchant_id` (`legacy/ddl/nvb_edw.dim_merchant.sql:25-26`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_merchant.batch_id` (`legacy/ddl/nvb_edw.dim_merchant.sql:27-28`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_merchant.load_ts` (`legacy/ddl/nvb_edw.dim_merchant.sql:29-30`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.dim_merchant.sql:32-36`): (merchant_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.dim_product` → `silver.dim_product` (`legacy/ddl/nvb_edw.dim_product.sql:1-37`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_edw.dim_product.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.dim_product.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `UNIQUE PRIMARY INDEX (product_id)` (`legacy/ddl/nvb_edw.dim_product.sql:22`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `product_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `product_name` | `VARCHAR(30) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 9 | `annual_rate` | `DECIMAL(6,2)` | `DECIMAL(6,2)` |  |  |  |
| 10 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 11 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 12 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 13 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 14 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 15 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 16 | `product_family_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'RETAIL'` |  | `('RETAIL')` |
| 17 | `currency_cd` | `CHAR(3) CHARACTER SET LATIN` | `STRING` | `'NOK'` |  | `('NOK')` |
| 18 | `product_status_cd` | `VARCHAR(10) CHARACTER SET LATIN` | `STRING` | `'LIVE'` |  | `('LIVE','WITHDRAWN')` |
| 19 | `rate_basis_cd` | `VARCHAR(8) CHARACTER SET LATIN` | `STRING` | `'ACT365'` |  | `('ACT365','ACT360')` |
| 20 | `minimum_balance` | `DECIMAL(18,2)` | `DECIMAL(18,2)` | `0.00` | `CHECK (minimum_balance >= 0)` |  |

- `COMMENT ON TABLE nvb_edw.dim_product` (`legacy/ddl/nvb_edw.dim_product.sql:24-25`): 'Current retail banking reference attributes; corrections overwrite the current record.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_product.product_id` (`legacy/ddl/nvb_edw.dim_product.sql:26-27`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_product.batch_id` (`legacy/ddl/nvb_edw.dim_product.sql:28-29`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.dim_product.load_ts` (`legacy/ddl/nvb_edw.dim_product.sql:30-31`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.dim_product.sql:33-37`): (product_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.etl_batch_control` → `silver.etl_batch_control` (`legacy/ddl/nvb_edw.etl_batch_control.sql:1-38`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_edw.etl_batch_control.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.etl_batch_control.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (batch_date)` (`legacy/ddl/nvb_edw.etl_batch_control.sql:23`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `batch_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` |  |  |  |
| 8 | `job_name` | `VARCHAR(30) CHARACTER SET UNICODE` | `STRING` |  |  |  |
| 9 | `status` | `VARCHAR(10) CHARACTER SET UNICODE` | `STRING` |  |  |  |
| 10 | `row_count` | `BIGINT` | `BIGINT` |  |  |  |
| 11 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 12 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 13 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 14 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 15 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 16 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 17 | `last_step_cd` | `VARCHAR(30) CHARACTER SET LATIN` | `STRING` | `'10_close_batch'` |  |  |
| 18 | `window_start_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 19 | `window_end_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2025-06-30'` |  |  |
| 20 | `reject_count` | `BIGINT` | `BIGINT` | `0` | `CHECK (reject_count >= 0)` |  |
| 21 | `scheduler_group_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'NVB_NIGHTLY'` |  |  |

- `COMMENT ON TABLE nvb_edw.etl_batch_control` (`legacy/ddl/nvb_edw.etl_batch_control.sql:25-26`): 'Retail banking ledger and processing records in NOK; retained for control and reporting.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.etl_batch_control.batch_date` (`legacy/ddl/nvb_edw.etl_batch_control.sql:27-28`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.etl_batch_control.batch_id` (`legacy/ddl/nvb_edw.etl_batch_control.sql:29-30`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.etl_batch_control.load_ts` (`legacy/ddl/nvb_edw.etl_batch_control.sql:31-32`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.etl_batch_control.sql:34-38`): (batch_date), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.fct_daily_balance` → `silver.fct_daily_balance` (`legacy/ddl/nvb_edw.fct_daily_balance.sql:1-37`)

- Table kind: `CREATE MULTISET TABLE` (`legacy/ddl/nvb_edw.fct_daily_balance.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.fct_daily_balance.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (account_id)` (`legacy/ddl/nvb_edw.fct_daily_balance.sql:21`); partitioning `PARTITION BY RANGE_N(balance_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30' EACH INTERVAL '1' MONTH)` (`legacy/ddl/nvb_edw.fct_daily_balance.sql:22`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `account_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `balance_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` |  |  |  |
| 9 | `balance` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 10 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 11 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 12 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 13 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 14 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 15 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 16 | `balance_type_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'BOOK'` |  | `('BOOK','AVAILABLE')` |
| 17 | `currency_cd` | `CHAR(3) CHARACTER SET LATIN` | `STRING` | `'NOK'` |  | `('NOK')` |
| 18 | `accrued_interest` | `DECIMAL(18,2)` | `DECIMAL(18,2)` | `0.00` |  |  |
| 19 | `reserved_amount` | `DECIMAL(18,2)` | `DECIMAL(18,2)` | `0.00` |  |  |

- `COMMENT ON TABLE nvb_edw.fct_daily_balance` (`legacy/ddl/nvb_edw.fct_daily_balance.sql:24-25`): 'Retail banking ledger and processing records in NOK; retained for control and reporting.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_daily_balance.account_id` (`legacy/ddl/nvb_edw.fct_daily_balance.sql:26-27`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_daily_balance.batch_id` (`legacy/ddl/nvb_edw.fct_daily_balance.sql:28-29`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_daily_balance.load_ts` (`legacy/ddl/nvb_edw.fct_daily_balance.sql:30-31`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.fct_daily_balance.sql:33-37`): (account_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.fct_loan` → `silver.fct_loan` (`legacy/ddl/nvb_edw.fct_loan.sql:1-38`)

- Table kind: `CREATE MULTISET TABLE` (`legacy/ddl/nvb_edw.fct_loan.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.fct_loan.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (loan_id)` (`legacy/ddl/nvb_edw.fct_loan.sql:23`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `loan_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `account_id` | `INTEGER` | `INT` |  |  |  |
| 9 | `outstanding` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 10 | `arrears_days` | `INTEGER` | `INT` |  |  |  |
| 11 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 12 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 13 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 14 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 15 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 16 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 17 | `loan_status_cd` | `VARCHAR(10) CHARACTER SET LATIN` | `STRING` | `'LIVE'` |  | `('LIVE','CLOSED')` |
| 18 | `currency_cd` | `CHAR(3) CHARACTER SET LATIN` | `STRING` | `'NOK'` |  | `('NOK')` |
| 19 | `contractual_rate` | `DECIMAL(6,2)` | `DECIMAL(6,2)` | `5.25` | `CHECK (contractual_rate >= 0)` |  |
| 20 | `repayment_frequency_cd` | `VARCHAR(10) CHARACTER SET LATIN` | `STRING` | `'MONTHLY'` |  | `('MONTHLY','QUARTERLY')` |
| 21 | `collateral_type_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'PROPERTY'` |  | `('PROPERTY','UNSECURED')` |

- `COMMENT ON TABLE nvb_edw.fct_loan` (`legacy/ddl/nvb_edw.fct_loan.sql:25-26`): 'Retail banking ledger and processing records in NOK; retained for control and reporting.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_loan.loan_id` (`legacy/ddl/nvb_edw.fct_loan.sql:27-28`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_loan.batch_id` (`legacy/ddl/nvb_edw.fct_loan.sql:29-30`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_loan.load_ts` (`legacy/ddl/nvb_edw.fct_loan.sql:31-32`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.fct_loan.sql:34-38`): (loan_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.fct_product_order` → `silver.fct_product_order` (`legacy/ddl/nvb_edw.fct_product_order.sql:1-38`)

- Table kind: `CREATE MULTISET TABLE` (`legacy/ddl/nvb_edw.fct_product_order.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.fct_product_order.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (order_id)` (`legacy/ddl/nvb_edw.fct_product_order.sql:23`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `order_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `customer_id` | `INTEGER` | `INT` |  |  |  |
| 9 | `product_id` | `INTEGER` | `INT` |  |  |  |
| 10 | `order_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` |  |  |  |
| 11 | `status` | `VARCHAR(10) CHARACTER SET UNICODE` | `STRING` |  |  |  |
| 12 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 13 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 14 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 15 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 16 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 17 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 18 | `application_channel_cd` | `VARCHAR(10) CHARACTER SET LATIN` | `STRING` | `'ONLINE'` |  | `('ONLINE','BRANCH','MOBILE')` |
| 19 | `decision_system_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'RETAIL_RULES'` |  |  |
| 20 | `currency_cd` | `CHAR(3) CHARACTER SET LATIN` | `STRING` | `'NOK'` |  | `('NOK')` |
| 21 | `manual_review_ind` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'N'` |  | `('N','Y')` |

- `COMMENT ON TABLE nvb_edw.fct_product_order` (`legacy/ddl/nvb_edw.fct_product_order.sql:25-26`): 'Retail banking ledger and processing records in NOK; retained for control and reporting.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_product_order.order_id` (`legacy/ddl/nvb_edw.fct_product_order.sql:27-28`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_product_order.batch_id` (`legacy/ddl/nvb_edw.fct_product_order.sql:29-30`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_product_order.load_ts` (`legacy/ddl/nvb_edw.fct_product_order.sql:31-32`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.fct_product_order.sql:34-38`): (order_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_edw.fct_transaction` → `silver.fct_transaction` (`legacy/ddl/nvb_edw.fct_transaction.sql:1-40`)

- Table kind: `CREATE MULTISET TABLE` (`legacy/ddl/nvb_edw.fct_transaction.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_edw.fct_transaction.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (transaction_id)` (`legacy/ddl/nvb_edw.fct_transaction.sql:24`); partitioning `PARTITION BY RANGE_N(txn_date BETWEEN DATE '2024-01-01' AND DATE '2025-06-30' EACH INTERVAL '1' MONTH)` (`legacy/ddl/nvb_edw.fct_transaction.sql:25`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `transaction_id` | `BIGINT` | `BIGINT` |  |  |  |
| 8 | `account_id` | `INTEGER` | `INT` |  |  |  |
| 9 | `txn_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` |  |  |  |
| 10 | `amount` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 11 | `channel` | `VARCHAR(10) CHARACTER SET UNICODE` | `STRING` |  |  |  |
| 12 | `merchant_id` | `INTEGER` | `INT` |  |  |  |
| 13 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 14 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 15 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 16 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 17 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 18 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |
| 19 | `currency_cd` | `CHAR(3) CHARACTER SET LATIN` | `STRING` | `'NOK'` |  | `('NOK')` |
| 20 | `posting_status_cd` | `VARCHAR(10) CHARACTER SET LATIN` | `STRING` | `'POSTED'` |  | `('POSTED','PENDING')` |
| 21 | `reversal_ind` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'N'` |  | `('N','Y')` |
| 22 | `clearing_scheme_cd` | `VARCHAR(12) CHARACTER SET LATIN` | `STRING` | `'DOMESTIC'` |  | `('DOMESTIC','CROSSBORDER')` |

- `COMMENT ON TABLE nvb_edw.fct_transaction` (`legacy/ddl/nvb_edw.fct_transaction.sql:27-28`): 'Retail banking ledger and processing records in NOK; retained for control and reporting.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_transaction.transaction_id` (`legacy/ddl/nvb_edw.fct_transaction.sql:29-30`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_transaction.batch_id` (`legacy/ddl/nvb_edw.fct_transaction.sql:31-32`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_edw.fct_transaction.load_ts` (`legacy/ddl/nvb_edw.fct_transaction.sql:33-34`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_edw.fct_transaction.sql:36-40`): (transaction_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_mart.mart_branch_performance` → `gold.mart_branch_performance` (`legacy/ddl/nvb_mart.mart_branch_performance.sql:1-33`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_mart.mart_branch_performance.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_mart.mart_branch_performance.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (branch_id)` (`legacy/ddl/nvb_mart.mart_branch_performance.sql:18`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `branch_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `customer_count` | `INTEGER` | `INT` |  |  |  |
| 9 | `net_amount` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 10 | `league_rank` | `INTEGER` | `INT` |  |  |  |
| 11 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 12 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 13 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 14 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 15 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 16 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |

- `COMMENT ON TABLE nvb_mart.mart_branch_performance` (`legacy/ddl/nvb_mart.mart_branch_performance.sql:20-21`): 'Published retail banking control summary; released after nightly reconciliation.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_branch_performance.branch_id` (`legacy/ddl/nvb_mart.mart_branch_performance.sql:22-23`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_branch_performance.batch_id` (`legacy/ddl/nvb_mart.mart_branch_performance.sql:24-25`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_branch_performance.load_ts` (`legacy/ddl/nvb_mart.mart_branch_performance.sql:26-27`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_mart.mart_branch_performance.sql:29-33`): (branch_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_mart.mart_card_spend_category` → `gold.mart_card_spend_category` (`legacy/ddl/nvb_mart.mart_card_spend_category.sql:1-32`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_mart.mart_card_spend_category.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_mart.mart_card_spend_category.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (category)` (`legacy/ddl/nvb_mart.mart_card_spend_category.sql:17`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `category` | `VARCHAR(15) CHARACTER SET UNICODE` | `STRING` |  |  |  |
| 8 | `txn_count` | `INTEGER` | `INT` |  |  |  |
| 9 | `spend` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 10 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 11 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 12 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 13 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 14 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 15 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |

- `COMMENT ON TABLE nvb_mart.mart_card_spend_category` (`legacy/ddl/nvb_mart.mart_card_spend_category.sql:19-20`): 'Published retail banking control summary; released after nightly reconciliation.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_card_spend_category.category` (`legacy/ddl/nvb_mart.mart_card_spend_category.sql:21-22`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_card_spend_category.batch_id` (`legacy/ddl/nvb_mart.mart_card_spend_category.sql:23-24`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_card_spend_category.load_ts` (`legacy/ddl/nvb_mart.mart_card_spend_category.sql:25-26`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_mart.mart_card_spend_category.sql:28-32`): (category), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_mart.mart_channel_daily` → `gold.mart_channel_daily` (`legacy/ddl/nvb_mart.mart_channel_daily.sql:1-33`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_mart.mart_channel_daily.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_mart.mart_channel_daily.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (txn_date)` (`legacy/ddl/nvb_mart.mart_channel_daily.sql:18`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `txn_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` |  |  |  |
| 8 | `channel` | `VARCHAR(10) CHARACTER SET UNICODE` | `STRING` |  |  |  |
| 9 | `txn_count` | `INTEGER` | `INT` |  |  |  |
| 10 | `net_amount` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 11 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 12 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 13 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 14 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 15 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 16 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |

- `COMMENT ON TABLE nvb_mart.mart_channel_daily` (`legacy/ddl/nvb_mart.mart_channel_daily.sql:20-21`): 'Published retail banking control summary; released after nightly reconciliation.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_channel_daily.txn_date` (`legacy/ddl/nvb_mart.mart_channel_daily.sql:22-23`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_channel_daily.batch_id` (`legacy/ddl/nvb_mart.mart_channel_daily.sql:24-25`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_channel_daily.load_ts` (`legacy/ddl/nvb_mart.mart_channel_daily.sql:26-27`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_mart.mart_channel_daily.sql:29-33`): (txn_date), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_mart.mart_customer_segment` → `gold.mart_customer_segment` (`legacy/ddl/nvb_mart.mart_customer_segment.sql:1-34`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_mart.mart_customer_segment.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_mart.mart_customer_segment.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (customer_id)` (`legacy/ddl/nvb_mart.mart_customer_segment.sql:19`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `customer_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `txn_count` | `INTEGER` | `INT` |  |  |  |
| 9 | `spend` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 10 | `last_txn_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` |  |  |  |
| 11 | `segment` | `VARCHAR(12) CHARACTER SET UNICODE` | `STRING` |  |  |  |
| 12 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 13 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 14 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 15 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 16 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 17 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |

- `COMMENT ON TABLE nvb_mart.mart_customer_segment` (`legacy/ddl/nvb_mart.mart_customer_segment.sql:21-22`): 'Published retail banking control summary; released after nightly reconciliation.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_customer_segment.customer_id` (`legacy/ddl/nvb_mart.mart_customer_segment.sql:23-24`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_customer_segment.batch_id` (`legacy/ddl/nvb_mart.mart_customer_segment.sql:25-26`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_customer_segment.load_ts` (`legacy/ddl/nvb_mart.mart_customer_segment.sql:27-28`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_mart.mart_customer_segment.sql:30-34`): (customer_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_mart.mart_monthly_balance` → `gold.mart_monthly_balance` (`legacy/ddl/nvb_mart.mart_monthly_balance.sql:1-33`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_mart.mart_monthly_balance.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_mart.mart_monthly_balance.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (account_id)` (`legacy/ddl/nvb_mart.mart_monthly_balance.sql:18`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `account_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `month_start` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` |  |  |  |
| 9 | `average_balance` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 10 | `closing_balance` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 11 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 12 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 13 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 14 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 15 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 16 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |

- `COMMENT ON TABLE nvb_mart.mart_monthly_balance` (`legacy/ddl/nvb_mart.mart_monthly_balance.sql:20-21`): 'Published retail banking control summary; released after nightly reconciliation.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_monthly_balance.account_id` (`legacy/ddl/nvb_mart.mart_monthly_balance.sql:22-23`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_monthly_balance.batch_id` (`legacy/ddl/nvb_mart.mart_monthly_balance.sql:24-25`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_monthly_balance.load_ts` (`legacy/ddl/nvb_mart.mart_monthly_balance.sql:26-27`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_mart.mart_monthly_balance.sql:29-33`): (account_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_mart.mart_product_profitability` → `gold.mart_product_profitability` (`legacy/ddl/nvb_mart.mart_product_profitability.sql:1-32`)

- Table kind: `CREATE SET TABLE` (`legacy/ddl/nvb_mart.mart_product_profitability.sql:1`); options `FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_mart.mart_product_profitability.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `PRIMARY INDEX (product_id)` (`legacy/ddl/nvb_mart.mart_product_profitability.sql:17`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `product_id` | `INTEGER` | `INT` |  |  |  |
| 8 | `average_balance` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 9 | `annual_income` | `DECIMAL(18,2)` | `DECIMAL(18,2)` |  |  |  |
| 10 | `batch_id` | `BIGINT NOT NULL` | `BIGINT NOT NULL` | `20250630` |  |  |
| 11 | `load_ts` | `TIMESTAMP(0) NOT NULL` | `TIMESTAMP NOT NULL` | `TIMESTAMP '2025-06-30 23:00:00'` |  |  |
| 12 | `src_system_cd` | `VARCHAR(12) CHARACTER SET LATIN NOT CASESPECIFIC` | `STRING` | `'COREBANK'` |  | `('COREBANK','CLEARING','CRM')` |
| 13 | `record_status_cd` | `CHAR(1) CHARACTER SET LATIN` | `STRING` | `'A'` | `CHECK (record_status_cd IN ('A','D'))` | `('A','D')` |
| 14 | `effective_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '2024-01-01'` |  |  |
| 15 | `expiry_date` | `DATE FORMAT 'YYYY-MM-DD'` | `DATE` | `DATE '9999-12-31'` |  |  |

- `COMMENT ON TABLE nvb_mart.mart_product_profitability` (`legacy/ddl/nvb_mart.mart_product_profitability.sql:19-20`): 'Published retail banking control summary; released after nightly reconciliation.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_product_profitability.product_id` (`legacy/ddl/nvb_mart.mart_product_profitability.sql:21-22`): 'Business identifier used by nightly reconciliation and consumer joins.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_product_profitability.batch_id` (`legacy/ddl/nvb_mart.mart_product_profitability.sql:23-24`): 'Delivery identifier assigned by overnight operations.' → `COMMENT`
- `COMMENT ON COLUMN nvb_mart.mart_product_profitability.load_ts` (`legacy/ddl/nvb_mart.mart_product_profitability.sql:25-26`): 'Logical delivery timestamp; fixed for repeatable restarts of the same delivery.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_mart.mart_product_profitability.sql:28-32`): (product_id), (batch_id), (src_system_cd, record_status_cd) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_stg.customer_feed` → `bronze.customer_feed` (`legacy/ddl/nvb_stg.customer_feed.sql:1-20`)

- Table kind: `CREATE MULTISET TABLE` (`legacy/ddl/nvb_stg.customer_feed.sql:1`); options `NO FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_stg.customer_feed.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `NO PRIMARY INDEX` (`legacy/ddl/nvb_stg.customer_feed.sql:13`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `customer_id` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 8 | `customer_name` | `VARCHAR(160) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 9 | `branch_id` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 10 | `town` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 11 | `updated_at` | `VARCHAR(26) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |

- `COMMENT ON TABLE nvb_stg.customer_feed` (`legacy/ddl/nvb_stg.customer_feed.sql:15-16`): 'Retained upstream landing delivery; replace before the nightly integration run.' → `COMMENT`
- `COMMENT ON COLUMN nvb_stg.customer_feed.customer_id` (`legacy/ddl/nvb_stg.customer_feed.sql:17-18`): 'Raw upstream identifier; casting and validation occur in the integration job.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_stg.customer_feed.sql:20`): (customer_id) → dropped (Delta collects file-level min/max stats automatically).

### `nvb_stg.transaction_feed` → `bronze.transaction_feed` (`legacy/ddl/nvb_stg.transaction_feed.sql:1-21`)

- Table kind: `CREATE MULTISET TABLE` (`legacy/ddl/nvb_stg.transaction_feed.sql:1`); options `NO FALLBACK`, `NO BEFORE JOURNAL`, `NO AFTER JOURNAL`, `CHECKSUM = DEFAULT` (`legacy/ddl/nvb_stg.transaction_feed.sql:2-5`) → dropped (Teradata-only physical/journal options).
- Index: `NO PRIMARY INDEX` (`legacy/ddl/nvb_stg.transaction_feed.sql:14`).

| Line | Column | Teradata type / attributes | Delta type | Default → | CHECK → | COMPRESS |
|---|---|---|---|---|---|---|
| 7 | `transaction_id` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 8 | `account_id` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 9 | `txn_date` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 10 | `amount` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 11 | `channel` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |
| 12 | `merchant_id` | `VARCHAR(80) CHARACTER SET UNICODE NOT CASESPECIFIC` | `STRING` |  |  |  |

- `COMMENT ON TABLE nvb_stg.transaction_feed` (`legacy/ddl/nvb_stg.transaction_feed.sql:16-17`): 'Retained upstream landing delivery; replace before the nightly integration run.' → `COMMENT`
- `COMMENT ON COLUMN nvb_stg.transaction_feed.transaction_id` (`legacy/ddl/nvb_stg.transaction_feed.sql:18-19`): 'Raw upstream identifier; casting and validation occur in the integration job.' → `COMMENT`
- `COLLECT STATISTICS` (`legacy/ddl/nvb_stg.transaction_feed.sql:21`): (transaction_id) → dropped (Delta collects file-level min/max stats automatically).
