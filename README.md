# DAIWT-01 Lakehouse Migration

SQL-unit migration scaffold for the DAIWT-01 Databricks medallion target:
`bronze`, `silver` and `gold` in `daiwt_01_mig_dev` and `daiwt_01_mig_prod`.
There are 0 migrated units yet; `src/sql/units/registry.json` is `{}`.

## Source estate

[Cognition-Partner-Workshops-mirror/daiwt-01-teradata-warehouse](https://github.com/Cognition-Partner-Workshops-mirror/daiwt-01-teradata-warehouse)
models Teradata 17.x locally (mocked; no live database is required).
DDL is in `legacy/ddl`, BTEQ jobs 01–10 in `legacy/bteq`, and the schedule
is `legacy/schedules/nightly.yaml`.

The 20 source/control tables are:

- `nvb_stg.customer_feed`
- `nvb_stg.transaction_feed`
- `nvb_edw.dim_account`
- `nvb_edw.dim_branch`
- `nvb_edw.dim_calendar`
- `nvb_edw.dim_card`
- `nvb_edw.dim_customer`
- `nvb_edw.dim_merchant`
- `nvb_edw.dim_product`
- `nvb_edw.etl_batch_control`
- `nvb_edw.fct_daily_balance`
- `nvb_edw.fct_loan`
- `nvb_edw.fct_product_order`
- `nvb_edw.fct_transaction`
- `nvb_mart.mart_branch_performance`
- `nvb_mart.mart_card_spend_category`
- `nvb_mart.mart_channel_daily`
- `nvb_mart.mart_customer_segment`
- `nvb_mart.mart_monthly_balance`
- `nvb_mart.mart_product_profitability`

Goldens are a `snapshot` capture (`20261110T162154Z`) of the mocked estate: the legacy
generator's delivery plus row-level outputs materialized by its Python reference controls
(`golden/compute_baselines.py`), not a Teradata execution. They are staged at
`/Volumes/daiwt_01_mig_prod/landing/golden/_staging/20261110T162154Z/`; see
[golden/README.md](golden/README.md).


## Layout

| Path | Purpose |
| --- | --- |
| `databricks.yml` | Dev and production bundle targets |
| `src/sql/units/` | Migrated SQL files and `registry.json`; currently zero units |
| `src/parity/parity_check.py` | Golden CSV multiset comparison using SDK Files and SQL Statements APIs |
| `ci/dev_verify.sh` | Builder deployment, selected units, per-drop parity and attestation |
| `ci/attest.py` | Fail-closed Markdown attestation from parity JSON |
| `ci/reset.sh`, `ci/sweep_dev.sh` | Scoped cleanup |
| `tests/` | Offline pytest tests |

## Targets and authentication

- Dev: `daiwt_01_mig_dev`, schemas `bronze_pr_<n>`, `silver_pr_<n>`, `gold_pr_<n>`.
  The bundle owns these schemas and uses a separate workspace root per suffix.
- Prod: `daiwt_01_mig_prod`, fixed `bronze`, `silver`, `gold`; these and `landing`
  belong to Terraform, not this bundle. Production resources grant the deploying
  service principal CAN_MANAGE and workspace `users` CAN_VIEW.
- SQL warehouse: `6dc4b80538621b7b` (Serverless Starter Warehouse).
- Golden volume: `/Volumes/daiwt_01_mig_prod/landing/golden`.

Locally use the default Devin Databricks CLI profile and unified SDK authentication.
GitHub Actions uses Devin-CI with `DATABRICKS_AUTH_TYPE=github-oidc`, repository
variables `DATABRICKS_HOST` and `DATABRICKS_CLIENT_ID`, and `id-token: write`.
Every Databricks job sets environment `dev` or `prod` to match federation policy.
No GitHub secrets are required.

## Golden contract

The volume contains `inputs/`, `outputs/` and `manifest.json`. The manifest format is:

```json
{
  "drops": ["D0"],
  "files": [
    {
      "table": "gold.fact_sale",
      "path": "outputs/D0/load_sale/fact_sale.csv",
      "sha256": "<captured CSV hash>",
      "row_count": 1,
      "keys": ["sale_key"],
      "columns": [{"name": "sale_key", "source_type": "bigint"}, {"name": "amount", "source_type": "decimal"}]
    }
  ]
}
```

Drops come from manifest strings or objects with a `name`; a missing manifest defaults
to `D0`. Files under `outputs/` are outputs; inputs are excluded.
The checked-in `src/parity/contract.json` pins every output path and SHA-256
to its drop, producing unit, target table, kind, keys, row count and columns.
Generate a future contract with `python src/parity/build_contract.py --units /path/to/units.yaml`.
Table targets are cross-checked against explicit unit metadata; reports target
`gold.report_<unit>`. The contract is empty until the staged capture is promoted.
See [capture contract](golden/README.md) for staging and promotion requirements.
Runtime parity rejects missing/extra manifest output paths or differing hashes
before comparing any units. Target names are never inferred from CSV filenames.

Each output is a UTF-8 CSV with a header matching the ordered `columns` array.
Use SQL Statements API string representations for values (including decimal scale
and timestamps); `\N` represents SQL NULL. Empty strings and NULL are distinct.
Tables are identified by `<silver|gold>.<table>`; dev suffixes are resolved by
the harness. No sampling, tolerance, deduplication or ignored columns is applied.
Rows compare as multisets using row count and SHA-256 of sorted canonical JSON rows.

An existing volume with no manifest reports PASS with 0 goldens only when the
contract is also empty. A missing volume, denied access, malformed manifest, unreadable
migrated golden or failed SQL query fails closed. Each unregistered output reports
SKIP (`not yet migrated`) without reading its CSV or querying a target table.
Registration is determined by the contract's unit, including its report entries.
Registered outputs report PASS only for exact row-count and content matches.

## Adding a unit

1. Add `src/sql/units/<unit>.sql` and its bundle job resource, included from
   `databricks.yml`. Pass catalog and layer schema variables to the job, and name it
   `${var.job_name_prefix}<unit>` (dev `[pr_<n>] <unit>`, prod `<unit>`).
2. Register the bundle job key and all produced golden tables:

   ```json
   {"load_sale": {"job": "load_sale", "outputs": ["gold.fact_sale"], "owns": [], "depends_on": []}}
   ```

3. Add offline tests. Each output has exactly one producer. Dependencies determine
   execution order, with registry order breaking ties. See [registry documentation](docs/registry.md).

`classify_changes.sh` compares `BASE_SHA`/`HEAD_SHA` (default `origin/main`/`HEAD`).
SQL changes select their unit; registry or shared framework changes select all units.
README, workflow and test-only edits do not select unit jobs. Classification is recorded
as diagnostic evidence. Builder verification runs the selected units and checks parity for every manifest drop.

## CI

| Workflow | Trigger and behavior |
| --- | --- |
| `pr.yml` | PRs to `main` or `pre-migration`; offline tests, strict dev validation (no deploy), fail-closed pushed-head attestation check and sticky comment |
| `deploy-prod.yml` | Push to `main` or manual; strict prod validation, bundle deployment, all registered production units and prod parity |
| `reset.yml` | Manual `target=dev|prod`; runs only if `confirm` exactly matches target |
| `dev-sweep.yml` | Manual; removes dev PR deployments for closed PRs, or a specified suffix |

Reset dev removes only `bronze|silver|gold_pr_<n>` schemas bearing this bundle's
comment marker and destroys their suffix-scoped bundle jobs. Sweep uses the same
scope but only for closed PR numbers obtained from GitHub. Production reset destroys
this bundle's production jobs and drops only tables listed in the units registry;
it leaves Terraform-owned schemas in place. `landing` and its golden volume are
never cleanup targets. Cleanup must run as the deployment principal (the builder for builder-owned dev
deployments), so bundle state resolves to the same workspace user root as deployment.
There are currently no production tables or jobs to reset.

## Builder flow

At a clean, pushed HEAD, use the default Devin CLI profile:

```bash
bash ci/dev_verify.sh pr_<n>
# Optional selection: bash ci/dev_verify.sh pr_<n> unit_a,unit_b
# All units: bash ci/dev_verify.sh pr_<n> '*'
```

The builder resolves the PR base with `gh pr view` (falling back to `origin/main`),
diffs `origin/<base>...HEAD`, expands dependants and upstream dependencies,
validates and deploys dev, cleans selected owned objects,
then runs units and parity for every manifest drop. Unit jobs receive only the
`drop` parameter. It renders `attestation.md` and inserts evidence into
`daiwt_01_mig_dev.default.pr_attestation`, creating the table if necessary.
Rows include head SHA, suffix, status, units, selection, drops, Markdown,
run URLs, recorded identity and timestamp.

Dev parity receives the executed unit list and skips registered units outside that
list with `not run in this verification`. Production always compares every registered unit.

PR CI never deploys or executes unit jobs. It requires the latest row for the
exact head SHA and `pr_<n>` suffix to be PASS, every current manifest drop to
be PASS (with exactly the same drop set), and recorded units to cover the
classification of `base...head`. Missing or stale evidence fails closed.
All workflow concurrency groups use `cancel-in-progress: false`.

## Local checks

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
databricks bundle validate --strict -t prod
databricks bundle validate --strict -t dev --var schema_suffix=pr_1
python src/parity/parity_check.py --suffix pr_1 --drop D0
python ci/attest.py
```

Parity writes per-drop JSON under `out/`; attestation writes `attestation.md`.
With zero goldens, PASS means only that the empty contract is consistent, not migration parity.
Future unregistered outputs are SKIP (not yet migrated), not compared parity evidence.
