# Card reference dimension

`card.pipeline.sql` publishes the full typed `asof_edw_dim_card(:drop)` slice,
including inactive records. All fourteen DDL columns are present in the captured
feed, so no missing-column defaults are needed. Teradata character types map to
STRING and the timezone-free load timestamp maps to TIMESTAMP_NTZ, as exposed by
the shared accessor. There are no golden contract outputs; the unit owns
`silver.dim_card` and `silver.dim_card_quarantine`.

Required keys/audit fields and the nullable DDL record-status check are Lakeflow
drop-row expectations. Their complement is retained with a reason in quarantine.
`${silver_bad_rows}` adds one synthetic null-key row only when set to `true`.

`python -m pytest -q` discovers `test_card.py` without shared test configuration.
The offline harness executes the SQL projections and predicates in SQLite with
strict Python casts for the declared Delta types; it is not a Lakeflow compiler.
It covers D0 control metrics, every projected value, empty input, invalid casts,
quarantine, inactive/null status, and both bad-row settings.

`d0_dim_card.csv.gz` is the unchanged D0 pipe-delimited input from the read-only
landing snapshot, gzip-compressed with no timestamp. `d0_dim_card.expected.json`
is the unchanged `controls/D0/expected/nvb_edw.dim_card.json` baseline. Tests pin
both decompressed input and control bytes against `golden/manifest.json` SHA-256s.
