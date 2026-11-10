# Product reference dimension

`silver_pipeline` refreshes `dim_product` from the sparse-drop-safe
`asof_edw_dim_product(:drop)` accessor. The projection follows the legacy DDL
column order and Databricks type mappings, including `TIMESTAMP_NTZ` and exact
decimal scales. All 14 columns are present in the captured feed; no missing-column
defaults are needed. Nullable attributes and deleted (`D`) records are preserved.

Expectations reject missing product keys, batch IDs and load timestamps, invalid
record statuses and negative minimum balances. `dim_product_quarantine` retains
the rejected candidate rows. Bronze and the accessor already enforce source
types; the Silver projection retains those types. Decimal casts go through the
printed string representation. `${silver_bad_rows}` adds one null-key candidate
when enabled in development; the bundle fixes this switch to false in production.

This directly loaded dimension has metric controls rather than a row-level parity
output, so both published tables are registered in `owns` with `foundation` metadata.

## Offline verification

Run from the repository root with the development virtualenv:

```sh
python -m pytest -q
```

The adjacent fixtures are byte-for-byte copies of the golden volume files:

- `product_D0.csv`: `inputs/D0/nvb_edw.dim_product/nvb_edw.dim_product.csv`
- `product_D0.expected.json`: `controls/D0/expected/nvb_edw.dim_product.json`

Tests pin their SHA-256 hashes to `golden/manifest.json`, check the SQL projection
and registry contract, and recompute all product metrics (counts, distincts,
sums, date ranges and code distributions). Python models the declared casts;
SQLite executes the actual expectation and quarantine predicates against normal,
empty, null, malformed-key, invalid-status and negative-balance cases. Demo-on
and demo-off candidate partitions are checked offline. These checks do not compile
or execute Lakeflow SQL; deployment and runtime validation belong to `dev_verify`.
