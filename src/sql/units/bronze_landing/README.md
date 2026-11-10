# Teradata golden input landing

SQL-file load/check tasks use the bundle warehouse and target permissions. All
11 delivered CSV inputs are read from `:golden_path/inputs/<drop>/<table>/<table>.csv`.
No outputs or parquet alternatives are loaded. D0 metadata and counts are pinned
from golden/manifest.json; unknown drops fail before any DELETE. Add a new drop's
metadata and expected counts before replaying it.

| Legacy input | Bronze name | D0 rows | Non-null key check |
| --- | --- | ---: | --- |
| nvb_edw.dim_account | edw_dim_account | 8000 | account_id |
| nvb_edw.dim_branch | edw_dim_branch | 8 | branch_id |
| nvb_edw.dim_calendar | edw_dim_calendar | 547 | calendar_date |
| nvb_edw.dim_card | edw_dim_card | 6000 | card_id |
| nvb_edw.dim_merchant | edw_dim_merchant | 80 | merchant_id |
| nvb_edw.dim_product | edw_dim_product | 4 | product_id |
| nvb_edw.fct_daily_balance | edw_fct_daily_balance | 4376000 | account_id, balance_date |
| nvb_edw.fct_loan | edw_fct_loan | 1800 | loan_id |
| nvb_edw.fct_product_order | edw_fct_product_order | 3500 | order_id |
| nvb_stg.customer_feed | stg_customer_feed | 5100 | customer_id |
| nvb_stg.transaction_feed | stg_transaction_feed | 180000 | transaction_id |

## Types and byte conventions

Column order and types follow each corresponding legacy/ddl/<table>.sql.
INTEGER -> INT; BIGINT, DATE and every DECIMAL(p,s) retain their precision/scale.
TIMESTAMP(0) -> TIMESTAMP_NTZ preserves the source wall clock without a session
zone shift (delivered timestamps use YYYY-MM-DD HH:mm:ss). Values are not rounded
to seconds on ingestion; the captured delivery has second resolution.
CHAR/VARCHAR -> STRING deliberately preserves delivered whitespace and does not
pad, truncate, case-fold or implement Teradata NOT CASESPECIFIC collation. Lengths,
compression, indexes, defaults and CHECK constraints are not reimplemented in raw
bronze. Explicit source NOT NULL on batch_id/load_ts is retained. Both staging
feeds are entirely STRING per their raw VARCHAR DDL, including IDs, dates and
amounts: parsing/cleaning belongs to downstream units, not bronze.

The manifest specifies UTF-8, pipe separators, header and LF. Read-only samples
of the downloaded dim_account (8000 rows), customer_feed (5100 rows), and the
first 8 KiB of dim_product, transaction_feed and fct_daily_balance contained no
empty fields or backslash-N tokens. Only the large balance file's head was read.
The source data/generate.py:59 uses pandas to_csv without na_rep: empty and null
strings have no distinct delivered encoding. STRING columns therefore preserve
empty tokens as empty strings; empty typed numeric/date/time tokens become NULL.
No backslash-N conversion is done: that marker belongs to output goldens, not
this input delivery. Quotes and doubled quote escapes follow CSV parsing, with
multiline enabled; leading/trailing spaces are preserved.

ANSI CAST rejects malformed/overflowing typed values, FAILFAST rejects malformed
CSV, enforceSchema=false checks headers, and rescued data raises rather than
being silently dropped. Source strings are intentionally not TRY_CAST in bronze.
Decimal casts retain exact declared scale; excess fractional digits follow Spark
HALF_UP cast behavior. No floating point intermediates are used.

## Replay and lineage

Each table DELETEs only `_drop = :drop` then INSERTs that delivery (same pattern as
the reference). These are separate commits, not an all-table transaction; a failed
replay may leave a partial slice and must be rerun successfully before consumers
are released. Do not run concurrent replays of the same drop.

Every row carries _drop STRING, _batch_id BIGINT, _business_date DATE,
_source_file STRING from _metadata.file_path, and _loaded_at TIMESTAMP. D0 maps
to 20250630 / 2025-06-30, independently of source batch_id and load_ts, which are
kept unchanged. Self-checks require manifest row counts, non-null keys and all
lineage fields. Duplicates are not discarded (customer feed contains corrections).
Silver as-of can enumerate delivery slices with SELECT _drop, max(_loaded_at)
FROM each bronze table GROUP BY _drop. This unit owns only the listed bronze
tables, publishes no golden outputs, and has no dependencies.
