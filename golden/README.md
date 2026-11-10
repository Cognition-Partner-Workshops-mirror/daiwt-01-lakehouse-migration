# Golden capture contract

## Source estate

[Cognition-Partner-Workshops-mirror/daiwt-01-teradata-warehouse](https://github.com/Cognition-Partner-Workshops-mirror/daiwt-01-teradata-warehouse)
models Teradata 17.x locally (mocked; no live database is required).
DDL is in `legacy/ddl`, BTEQ jobs 01–10 in `legacy/bteq`, and the schedule
is `legacy/schedules/nightly.yaml`.

The 20 source/control tables are:

- `nvb_stg.customer_feed`
- `nvb_stg.transaction_feed`
- `nvb_edw.dim_account`
- `nvb_edw.dim_branch`
- `nvb_edw.dim_calendar`
- `nvb_edw.dim_card`
- `nvb_edw.dim_customer`
- `nvb_edw.dim_merchant`
- `nvb_edw.dim_product`
- `nvb_edw.etl_batch_control`
- `nvb_edw.fct_daily_balance`
- `nvb_edw.fct_loan`
- `nvb_edw.fct_product_order`
- `nvb_edw.fct_transaction`
- `nvb_mart.mart_branch_performance`
- `nvb_mart.mart_card_spend_category`
- `nvb_mart.mart_channel_daily`
- `nvb_mart.mart_customer_segment`
- `nvb_mart.mart_monthly_balance`
- `nvb_mart.mart_product_profitability`

## Snapshot capture `20261110T162154Z`

There is no live Teradata. Capture mode is `snapshot`: `golden/capture_snapshot.py`
clones the legacy repository at commit `cf4b4fabd3df9c49f724b3087e590b68d28d8ef1`, runs
`make setup` (generator seed 1704 + `golden/compute_baselines.py`),
then a local-only `make reset S3=` to prove the delivery is deterministic and the
committed controls reproduce byte-for-byte. Delivered CSV md5s and row counts are
cross-checked against the S3 landing `_manifest.json`
(`s3://daiwt-01-landing-599083837640/daiwt-01/landing/`, read only).

Outputs come from the legacy repository's Python reference controls
(`transform()` in `golden/compute_baselines.py`), **not** from a Teradata execution.
Each derived table's metrics are recomputed from the materialized rows and must equal
its committed `golden/expected/<table>.json`; no value is corrected.

One delivery exists (batch 20250630), so there is one drop, `D0`.

| Input (pipe CSV + Parquet, as delivered) | Rows |
| --- | ---: |
| `nvb_edw.dim_account` | 8,000 |
| `nvb_edw.dim_branch` | 8 |
| `nvb_edw.dim_calendar` | 547 |
| `nvb_edw.dim_card` | 6,000 |
| `nvb_edw.dim_merchant` | 80 |
| `nvb_edw.dim_product` | 4 |
| `nvb_edw.fct_daily_balance` | 4,376,000 |
| `nvb_edw.fct_loan` | 1,800 |
| `nvb_edw.fct_product_order` | 3,500 |
| `nvb_stg.customer_feed` | 5,100 |
| `nvb_stg.transaction_feed` | 180,000 |

| Output | Target | Rows |
| --- | --- | ---: |
| `outputs/D0/customer/dim_customer.csv` | `silver.dim_customer` | 5,000 |
| `outputs/D0/transactions/fct_transaction.csv` | `silver.fct_transaction` | 180,000 |
| `outputs/D0/dormant/dim_account.csv` | `silver.dim_account` | 8,000 |
| `outputs/D0/monthly_balance/mart_monthly_balance.csv` | `gold.mart_monthly_balance` | 144,000 |
| `outputs/D0/customer_segment/mart_customer_segment.csv` | `gold.mart_customer_segment` | 5,000 |
| `outputs/D0/branch_league/mart_branch_performance.csv` | `gold.mart_branch_performance` | 8 |
| `outputs/D0/product_profitability/mart_product_profitability.csv` | `gold.mart_product_profitability` | 4 |
| `outputs/D0/channel/mart_channel_daily.csv` | `gold.mart_channel_daily` | 2,188 |
| `outputs/D0/card_category/mart_card_spend_category.csv` | `gold.mart_card_spend_category` | 4 |
| `outputs/D0/close_batch/etl_batch_control.csv` | `silver.etl_batch_control` | 1 |

`controls/D0/expected/*.json` and `controls/D0/summary.csv` are the legacy
reconciliation baselines, unchanged. `load_ts` (constant `2025-06-30 23:00:00`) is
omitted from output CSVs and listed in `excluded_from_parity`. CSV conventions are
recorded in the manifest. Provenance is in [`PROVENANCE.json`](PROVENANCE.json); branches
the snapshot cannot exercise (rejection/hold flows, scheduler steps, report exports,
views) are in [`skipped_branches.json`](skipped_branches.json).

Re-run (into an empty directory; existing captures are never overwritten):

```bash
python golden/capture_snapshot.py --capture-dir <dir> --commit <legacy sha>
python golden/capture_snapshot.py verify --capture-dir <dir>
```

Captured data is never committed; only the manifest, provenance and inventory are.

## Storage and promotion

The checked-in manifest is the staged snapshot capture; the parity contract is still
`{"outputs": []}` and the volume root has no manifest, so CI reports zero goldens until
promotion. Promoting requires generating the contract from this manifest in the same change.
Captured inputs, outputs and checksummed manifests belong in
`/Volumes/daiwt_01_mig_prod/landing/golden`.
Stage each capture at
`/Volumes/daiwt_01_mig_prod/landing/golden/_staging/<capture_id>/`.
Verify every staged file before operator-approved promotion, with the manifest last.
Do not upload or promote during scaffold validation.

Each future output must pin a drop, unit, explicit silver/gold target, keys,
ordered columns, row count and SHA-256 in `src/parity/contract.json`.
The harness rejects manifest/contract drift and compares CSV rows as multisets.

Generate a future contract using
`python src/parity/build_contract.py --units /path/to/units.yaml`.
The YAML must contain a `units` mapping with explicit target tables and golden paths.
No source-specific table-name inference is performed.
