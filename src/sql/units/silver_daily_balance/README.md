# Daily balance

Typed copy of `nvb_edw.fct_daily_balance`, in DDL column order. No status,
weekday, date-window, deduplication or account filter is applied. Nullable DDL
columns remain nullable, including the status CHECK's SQL NULL semantics.

The as-of accessor already supplies every DDL column: the captured feed includes
the defaults from `data/column_defaults.json` (batch 20250630, load timestamp
2025-06-30 23:00:00, COREBANK, A, effective date 2024-01-01, expiry date
9999-12-31, BOOK, NOK and zero accrued/reserved amounts). Pass those values
through rather than replacing explicit NULLs with defaults. The synthetic row
spells out these constants and changes only the status to exercise quarantine.
`${silver_bad_rows}` is false normally and always false in production.

The output expectations mirror the DDL's NOT NULL and status CHECK constraints;
violations are excluded from the output and retained in `daily_balance_quarantine`.
No extra business constraints are imposed on this directly loaded table.

There is no row-level golden output for this reference table. Registry policy
therefore requires `legacy_unit: foundation`, empty `outputs`, and both published
tables in `owns`. The source entity is legacy `daily_balance`.

## Offline verification

Install the repository requirements and this folder's `requirements-test.txt`.
Run `python -m pytest -q`. The SQL tests execute the actual SELECT bodies in
DuckDB, translating only Lakeflow wrappers, the as-of source and type aliases.
No Databricks writes are involved.

For full D0 metrics, provide the captured pipe-delimited CSV at
`golden/inputs/D0/nvb_edw.fct_daily_balance/nvb_edw.fct_daily_balance.csv`, or set
`DAILY_BALANCE_D0_CSV` to its path. Without the capture only that test skips.
Its SHA-256 is pinned by `golden/manifest.json`. The expected metrics fixture is
an unchanged copy of the legacy repository's committed
`golden/expected/nvb_edw.fct_daily_balance.json`, captured as
`controls/D0/expected/nvb_edw.fct_daily_balance.json` in the golden volume.
