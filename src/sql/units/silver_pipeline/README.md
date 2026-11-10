# Shared Silver Lakeflow job

`silver_pipeline` depends on `[silver_asof, sql_macros]`. Its YAML defines a
verify-only job when no entities exist. `silver_resources.py` discovers sorted
`src/sql/units/silver_*/*.pipeline.sql` files, excluding this scaffold. When any
exist it creates the `silver` pipeline and a Python-for-DABs job mutator adds `refresh_silver` before
`verify_silver`. There is no placeholder entity or empty-library pipeline.

The refresh is a full refresh of **all discovered entities**, not a file-scoped
refresh. `verify_silver` runs `src/parity/parity_check.py --layer silver` with the
job drop/catalog/suffix/warehouse, and a parity failure exits nonzero, visibly
failing the dependent task. Unmigrated outputs SKIP. The zero-entity job can
therefore verify with only SKIPs. Job concurrency is one.

## Builder contract

Create `src/sql/units/silver_<entity>/unit.json` and `<entity>.pipeline.sql`.
Set `job: silver_pipeline`; no per-entity job YAML is needed. For example (use real contract output names
and their associated legacy unit):

```json
{"job":"silver_pipeline","legacy_unit":"customer","outputs":["silver.dim_customer"],"owns":[],"depends_on":["silver_pipeline"]}
```

Declare any additional upstream entity dependencies explicitly. The registry
allows this shared job only for `silver_*` entities depending on the scaffold;
other job keys remain unique. Job lookup resolves to this scaffold's YAML.
`select_units` / `dev_verify.sh` expand dependencies as usual; `run_units` walks
topological order but executes each shared job key once, even for many selected
entities. Full refresh and layer verification include every deployed entity.

Use `CREATE OR REFRESH MATERIALIZED VIEW` for outputs or `CREATE OR REFRESH
PRIVATE MATERIALIZED VIEW` for intermediates, **never TEMPORARY VIEW**.
Declare exact golden column names, order and types. MVs containing TIMESTAMP_NTZ
must include `TBLPROPERTIES ('delta.feature.timestampNtz'='supported')` before AS.
Keep diagnostic columns out of golden-facing outputs.

Pipeline configuration supplies `${catalog}`, `${bronze_schema}`,
`${silver_schema}`, and `${silver_bad_rows}` for SQL substitution. Read a raw
exact-drop slice with `FROM ${catalog}.${bronze_schema}.edw_dim_account WHERE
_drop = :drop`, or prefer the sparse-drop-safe accessor:

```sql
CREATE OR REFRESH PRIVATE MATERIALIZED VIEW account_candidates
TBLPROPERTIES ('delta.feature.timestampNtz'='supported')
AS SELECT * FROM ${catalog}.${silver_schema}.asof_edw_dim_account(:drop);
```

The job's `drop` parameter flows to `refresh_silver.pipeline_task.parameters`,
then the pipeline's named `:drop` binding (default D0), and to parity's `--drop`.
Do **not** use static `${drop}` configuration substitution for the selected
delivery; runtime parameters do not override that static configuration.
`silver_bad_rows` remains a dev-only opt-in demo, false in production.

Use the active requirements-dev virtualenv for bundle validation/deployment.
Offline tests cover empty and populated resource shapes; validate both targets
again after adding an entity. Validation does not compile or execute pipeline SQL.
