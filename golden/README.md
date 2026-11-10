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

Golden capture is pending. Its source of reconciliation controls will be
`golden/compute_baselines.py` → `golden/expected/*.json` in the legacy repository.
These JSON controls are not row-level CSV goldens; a future capture must define
the CSV outputs and explicit target mapping before any unit can claim parity.
No captured inputs, outputs, provenance, or skipped-branch claims are included.

## Storage and promotion

The checked-in manifest is `{"drops": ["D0"], "files": []}` and the parity
contract is `{"outputs": []}`. Zero goldens means no migration parity evidence.
Future captured inputs, outputs and checksummed manifests belong in
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
