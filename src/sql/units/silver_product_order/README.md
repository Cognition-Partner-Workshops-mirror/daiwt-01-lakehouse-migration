# Product orders

`fct_product_order` is a typed, full-copy Silver MV over
`asof_edw_fct_product_order(:drop)`, including inactive records. Expectations
drop rows failing key, record-status and lineage checks from the output;
`fct_product_order_quarantine` also retains any rows failing those expectations,
including NULL status. The output contains only the 15 legacy DDL columns.
`${silver_bad_rows}` adds one synthetic invalid row only when enabled in dev.

The direct-delivery generator (`data/generate.py:36-47` in the legacy repo)
populates every metadata/default column before exporting. There is no product
order BTEQ transformation. Consequently this unit passes values through without
coalescing NULLs to defaults. Bronze already exposes DDL-typed columns and maps
empty feed strings to empty strings; Silver does not reinterpret them.

There is no product-order output in the parity contract. Registry ownership uses
`foundation`, no `outputs`, and both public MVs in `owns`, per `docs/registry.md`.

Offline tests are discovered by `python -m pytest -q`. The compressed D0 fixture
is the byte-identical S3 landing input pinned by snapshot `20261110T162154Z`; its
decompressed SHA-256 is checked against `golden/manifest.json`. The control JSON
is copied unchanged from the legacy `golden/expected/` baseline pinned as the
snapshot's `controls/D0/expected/` and is likewise
checksummed. Tests compare all table metrics (the control's `input_rows` describes
other deliveries and is not a metric of this table).
