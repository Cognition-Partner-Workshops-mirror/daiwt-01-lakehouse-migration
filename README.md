# daiwt_01

## Migration CI

Install `python -m pip install -r requirements-dev.txt`. Add a directory under
`src/units/<name>/` with `unit.yml` and ordered SQL files; no shared registry.
Run `migration-ci fetch-goldens` then `migration-ci check` (or
`migration-ci check --goldens /path/to/local/goldens`). `pytest --goldens PATH`
runs the same offline gate. Plain pytest validates structure without remote data.
PRs run offline checks only. Manually dispatch integrate for dev integration,
then promote for production. Reset destroys only the selected bundle and its
bronze/silver/gold/verification schemas, never the landing golden volume.
Framework fixes propagate by running `migration-ci sync .` from a new framework
version and opening a PR for the changed vendored files.

## Clean demo baseline

Framework version: **1.0.4**. There are no migrated units yet. The legacy source is
[daiwt-01-teradata-warehouse](https://github.com/Cognition-Partner-Workshops-mirror/daiwt-01-teradata-warehouse);
plain historical discovery references remain in `docs/discovery/`.

`golden/manifest.json` is the unchanged original manifest. `estate.yml` points to
a separate normalized D0 fixture volume, never to an overwritten original:
`/Volumes/daiwt_01_mig_prod/migration_ci_fixtures/golden`.
Its reviewed manifest is `golden/normalized-manifest.json`. This copy selects CSV
inputs over equivalent Parquet files, omits the auxiliary JSON input and control
files, converts pipe-delimited inputs to comma-delimited CSV, and uses portable
type metadata. Rows, column names and output table mappings are preserved.
The manifest records the original manifest and individual source-file hashes.
The original `/Volumes/daiwt_01_mig_prod/landing/golden` remains immutable.

The empty-estate offline gate validates framework wiring, not migrated business
logic. Integration lands D0 bronze and creates an empty parity-results table;
Understand and Migrate are separate later demo steps.
