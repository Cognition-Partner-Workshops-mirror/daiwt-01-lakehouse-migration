# Merchant reference dimension

Typed pass-through of `asof_edw_dim_merchant(:drop)` in legacy DDL order, including
deleted records. `TIMESTAMP(0)` maps to `TIMESTAMP_NTZ`. No defaults, trimming,
deduplication or category substitution are applied: `data/generate.py:37-47`
fills reference-table defaults before delivery and bronze preserves those values.
Nullable category is retained for downstream card-category classification.

The shared Silver pipeline owns refresh and verification. Neither table has a
golden output, so both are registered in `owns` with `legacy_unit: foundation`.
Expectations drop invalid keys, required batch/timestamp values and invalid/null
record status from the conformed dimension; quarantine retains those candidates.
`${silver_bad_rows}` adds one synthetic null-key row only when explicitly true.

Run offline tests with `python -m pytest -q` (pytest discovers this folder).
The tests execute the SQL SELECTs and expectation predicates in SQLite with a
small strict cast adapter; they do not compile Lakeflow SQL or emulate Spark.
The D0 merchant feed is reconstructed from the deterministic merchant expression
in legacy `data/generate.py:28` and its `data/column_defaults.json` values, without
requiring pandas, the legacy checkout or a network connection. Its bytes and the
embedded control metrics are pinned to `golden/manifest.json` SHA-256 entries.
Both were inspected from the golden volume using read-only `databricks fs cp`.
The column/type contract is transcribed from `legacy/ddl/nvb_edw.dim_merchant.sql`.
Warehouse compilation and pipeline execution remain separate deployment checks.
