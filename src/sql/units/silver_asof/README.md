# Bronze as-of accessors

Depends on `bronze_landing`. The SQL-file job creates and checks silver-schema
functions; it owns no tables or golden outputs. `legacy_unit: customer` is only
the shared framework's contract link, not output ownership.

`drop_seq(d)` accepts exactly uppercase D followed by digits representable as an
INT. D2 precedes D10; leading zeroes are accepted. Invalid, NULL, negative and
overflowing labels return NULL.

`resolve_drop(as_of_drop)` unions `_drop` / `_loaded_at` from **all 11 bronze
tables**, groups by label, and chooses the largest numeric sequence not greater
than the requested sequence. Equal sequences are ordered by most recent load
timestamp, then label descending for deterministic ties. Sparse requests such
as D9 with only D0 landed resolve to D0. Invalid requests or no eligible data
return NULL. Do not consume a partially replayed delivery: bronze checks must
finish successfully before this job or entity refresh runs.

Each `asof_<bronze table>(as_of_drop)` table function selects that resolved slice
and returns **only original source columns**, in bronze DDL order and types.
The five underscore-prefixed lineage columns are deliberately excluded; source
`batch_id` / `load_ts` remain. No candidate means an empty table result, not an
implicit latest delivery. Resolution is global, not per table.

`ci/bronze_schema.py` reads `bronze_landing/load.sql` CREATE TABLE definitions,
the single source of truth for columns. Regenerate functions, executable
snapshot self-checks, and bronze manifest job metadata using:

```sh
python ci/generate_silver_asof.py
```

Offline tests require generated files to match and check all table signatures.
Self-check SQL covers numeric ordering, invalid/NULL/overflow labels, missing
eligible data, and exact source-slice equality including duplicate rows via
EXCEPT ALL and counts. Warehouse execution is a separate verification step.
