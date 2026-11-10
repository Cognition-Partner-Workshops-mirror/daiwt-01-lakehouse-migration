# Verification policy and unit registry

Each unit is a directory `src/sql/units/<unit>/` containing all its SQL and
bundle resource YAML files plus `unit.json`. The directory name must match
`[a-z][a-z0-9_]*`. Every directory needs a registry file. Registry order is
alphabetical; execution is dependencies-first with alphabetical ties.

The frozen `unit.json` format has exactly these five keys:

```json
{
  "job": "silver_customers_job",
  "legacy_unit": "customer",
  "outputs": ["silver.customers"],
  "owns": ["bronze.customer_stage"],
  "depends_on": []
}
```

`job` is a bundle job key matching `[a-z][a-z0-9_]*`, normally unique and defined
in the unit directory's `*.job.yml`. The only sharing exception is a `silver_*`
entity with `job: silver_pipeline` and a direct `silver_pipeline` dependency:
its job is defined by the scaffold's YAML, conditionally augmented by
`silver_resources.py`. Entities need no job YAML. The runner executes each job
key once in dependency order; selecting entities refreshes all deployed entities
through the shared job, followed by visible Silver-layer parity.
`legacy_unit` names a source legacy unit
present in the parity contract outputs, or explicitly `foundation` for shared
units. Shared units publish no golden outputs; real output ownership must still
match that output's legacy unit. Every `outputs` table must be a
contract table belonging to that legacy unit. `owns` lists other written
bronze/silver/gold tables and must never contain contract tables. Each table
has exactly one owner across both lists and all units. Dependencies name
existing unit directories, with no cycles or self-dependencies. Bundle resources
are included by `src/sql/units/*/*.yml`; use bundle catalog/schema variables.

## Selection

The shared classifier diffs the merge-base of the PR base and head against head.
Changed or added files under a unit directory select that unit if it exists at
head. Removed units select nothing. Selection then adds transitive **upstream**
dependencies, never dependants. Explicit selections expand the same way; `*`
selects all units. Shared framework changes select all head units and require
verification only when selected units have golden outputs. `docs/`, `tests/`, `.github/`, `README.md`,
`.gitignore`, and `AGENTS.md` are ignored. Docs/test-only and removal-only changes
need no attestation.

## Fingerprints and builder

At a clean, pushed HEAD:

```bash
bash ci/dev_verify.sh pr_123
bash ci/dev_verify.sh pr_123 'silver_customers,another_unit'
bash ci/dev_verify.sh pr_123 '*'
```

The builder prints required and selected units, strictly validates and deploys
dev with only `schema_suffix`, cleans selected tables, runs selected jobs and
parity for **D0 only**, then records PASS/FAIL evidence in
`daiwt_01_mig_dev.default.unit_attestation`. Outputs without producers SKIP as
`not yet migrated`; unselected producers SKIP as `not selected`. Production
ignores selection and compares every registered output for every manifest drop.

The SHA-256 fingerprint covers sorted `<path>\t<blob sha>` lines for every file
in selected and transitive upstream unit directories plus every shared framework
file (tracked files outside unit directories, excluding ignored paths), followed
by the sorted expanded unit list. It is content-addressed, not commit-addressed:
rebasing over docs or unrelated unit additions preserves it, whereas shared or
upstream changes invalidate it.

PR CI never deploys. It queries all evidence for the PR suffix plus PASS evidence
by required-set fingerprint, newest first. A non-PASS newest row for this suffix
fails immediately, even if an earlier reusable PASS exists. Otherwise it accepts
the first PASS row with a superset of required units,
D0 PASS and a fingerprint matching the current head for that row's full unit set.
The check polls for up to 30 minutes; missing, malformed or stale evidence then
fails closed with a builder command. Selections without golden outputs skip
immediately without querying Unity Catalog.
The row also stores head SHA, suffix, status, drops, Markdown, run URLs, identity
and timestamp. Cleanup uses both ownership lists; landing is never a target.

## Nightly wrapper

`nightly_resources:load_resources` creates `nvb_nightly`, not a registry unit.
It reuses `registry.topo_order` and `run_units.execution_jobs` to collapse shared
Silver jobs. Each execution job runs once with the wrapper's `drop` parameter.
Registry edges are augmented by the legacy customer-to-close-batch order;
missing steps are linked over. An incompatible collapsed job DAG fails validation.
Foundation jobs precede other execution jobs. A registered `governance` job
depends on every non-failure task and runs with `NONE_FAILED`.

`batch_open` always precedes the foundation. It creates the exact contract
column list for `silver.etl_batch_control` (CHAR/VARCHAR become STRING), rejects
an already RUNNING batch, and opens the manifest-selected date/batch. A separate
`silver.nightly_run_state` records admission so a rejected run's failure handler
cannot overwrite the existing RUNNING batch. Initial window/effective dates are
left unset for the close-batch producer to populate; no new delivery date literals
are introduced. The contract excludes `load_ts`, so neither this table's DDL nor
the release report invents that excluded field.

`release_export` and `reconcile` require registered producers for both
`silver.etl_batch_control` and `silver.fct_transaction`. They persist the two
legacy control-report rows in `silver.nightly_control_report`, keyed by parent
run ID, remove reports older than 400 days, and raise on disagreement with the
COMPLETE batch and current transaction aggregates. These two auxiliary tables
are framework-owned, not golden outputs; they are not added to the unit registry.
Table rows replace the legacy text exports; no landing-volume writes are made.

`nightly_resources.VIEWS` declares the inputs of each of the three legacy views
and four reports. Registered `outputs` **and** `owns` identify producers, since
typed Silver dimensions have no golden output. A view task is omitted until all
its producers exist; report-on-view dependencies are explicit. When reconciliation
is available, every view waits for it. SQL templates in `src/orchestration/views/`
are passed to `create_view.sql`; it safely quotes schema identifiers and inserts
the validated manifest business date into persistent view DDL. No parameter marker
is stored in a view body. The channel window starts at the retained calendar's
minimum date and the finance report uses the delivery's year.

`batch_fail` depends on every other task with `AT_LEAST_ONE_FAILED`, identifies
the first failed task through the parent Jobs run, and marks an admitted batch
FAILED with that task key in `last_step_cd`. The Lakeflow run remains failed.
The schedule is paused by default (`nightly_pause_status`) and by the dev preset;
only explicit cutover approval should enable it. Foundation-only runs deliberately
cannot produce a COMPLETE batch: that belongs to the close-batch unit.
