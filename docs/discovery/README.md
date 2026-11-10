# Wave 0 discovery

Technical handover for the DAIWT-01 D0 migration of the Nordvik Sparebank Teradata 17 estate. The control date is `2025-06-30` and the batch is `20250630`.
Source paths refer to `daiwt-01-teradata-warehouse` unless they are marked as migration-repo paths.

- [Inventory and dependency graph](inventory.md): every BTEQ job, procedure, macro, view, report, DDL table and schedule job, with reads, writes and Mermaid lineage and job DAGs.
- [Unit manifest](units.json): registry unit names, waves, layers, dependencies, legacy sources, and the golden output and contract target table of each of the 10 contract units.
- [Target model](target_model.md): bronze/silver/gold tables with grains, keys, Delta types, temporal handling and golden rendering.
- [Business rules](business_rules.md): the semantic catalogue with D0 recomputation evidence and parity risks.
- [Builder notes](builder_notes.md): a per-unit implementation checklist.
- [Orchestration mapping](orchestration_mapping.md): `nightly.yaml` and the runbook mapped to one Lakeflow job, plus batch control, SLAs and re-homing of off-workflow jobs.
- [Shared macros and DDL](shared_macros.md): SQL UDF and macro candidates, Teradata to Delta type mapping, and the UC volumes the bundle needs.

## Unit list

| unit | wave | layer | target table(s) | golden output | upstream units |
|---|---|---|---|---|---|
| `bronze_landing` | 1 | bronze | `bronze.customer_feed`, `bronze.transaction_feed`, `bronze.dim_account`, `bronze.dim_branch`, `bronze.dim_calendar`, `bronze.dim_card`, `bronze.dim_merchant`, `bronze.dim_product`, `bronze.fct_daily_balance`, `bronze.fct_loan`, `bronze.fct_product_order` | none | — |
| `sql_macros` | 1 | platform | — | none | — |
| `silver_pipeline` | 1 | platform | — | none | bronze_landing |
| `governance` | 1 | platform | — | none | — |
| `orchestration` | 1 | platform | — | none | — |
| `branch` | 2 | silver | `silver.dim_branch` | none | bronze_landing |
| `product` | 2 | silver | `silver.dim_product` | none | bronze_landing |
| `merchant` | 2 | silver | `silver.dim_merchant` | none | bronze_landing |
| `calendar` | 2 | silver | `silver.dim_calendar` | none | bronze_landing |
| `card` | 2 | silver | `silver.dim_card` | none | bronze_landing |
| `daily_balance` | 2 | silver | `silver.fct_daily_balance` | none | bronze_landing |
| `loan` | 2 | silver | `silver.fct_loan` | none | bronze_landing |
| `product_order` | 2 | silver | `silver.fct_product_order` | none | bronze_landing |
| `customer` | 2 | silver | `silver.dim_customer` | `outputs/D0/customer/dim_customer.csv` | bronze_landing, sql_macros, branch |
| `transactions` | 2 | silver | `silver.fct_transaction` | `outputs/D0/transactions/fct_transaction.csv` | bronze_landing, sql_macros, merchant |
| `dormant` | 2 | silver | `silver.dim_account` | `outputs/D0/dormant/dim_account.csv` | bronze_landing, customer, transactions, branch |
| `close_batch` | 3 | silver | `silver.etl_batch_control` | `outputs/D0/close_batch/etl_batch_control.csv` | transactions, channel |
| `monthly_balance` | 3 | gold | `gold.mart_monthly_balance` | `outputs/D0/monthly_balance/mart_monthly_balance.csv` | daily_balance, dormant |
| `customer_segment` | 3 | gold | `gold.mart_customer_segment` | `outputs/D0/customer_segment/mart_customer_segment.csv` | customer, dormant, transactions |
| `branch_league` | 3 | gold | `gold.mart_branch_performance` | `outputs/D0/branch_league/mart_branch_performance.csv` | customer, dormant, transactions, branch |
| `product_profitability` | 3 | gold | `gold.mart_product_profitability` | `outputs/D0/product_profitability/mart_product_profitability.csv` | daily_balance, dormant, product |
| `channel` | 3 | gold | `gold.mart_channel_daily` | `outputs/D0/channel/mart_channel_daily.csv` | transactions, calendar |
| `card_category` | 3 | gold | `gold.mart_card_spend_category` | `outputs/D0/card_category/mart_card_spend_category.csv` | transactions, merchant |
| `business_views` | 3 | gold | `gold.v_customer_portfolio`, `gold.v_credit_risk`, `gold.v_channel_trend`, `gold.report_branch_league`, `gold.report_card_services`, `gold.report_finance_monthly`, `gold.report_risk_arrears` | none | customer, branch, dormant, loan, calendar, customer_segment, branch_league, monthly_balance, channel, card_category |
