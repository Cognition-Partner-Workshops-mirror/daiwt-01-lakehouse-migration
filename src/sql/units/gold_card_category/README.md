# gold_card_category

Legacy `09_card_category.btq` + `legacy/macros/card_category.sql` → `gold.mart_card_spend_category`
(golden `outputs/D0/card_category/mart_card_spend_category.csv`, 4 rows).

Job `${var.job_name_prefix}gold_card_category`, parameters `drop`, `window_start` (2024-01-01),
`as_of_date` (2025-06-30) and `batch_id` (20250630):

1. `mart_card_spend_category` (`card_category.sql`): creates the table in DDL column order,
   runs the two BTEQ controls as pre-write `raise_error` checks (control aggregate without
   window/status filters: NULL category or negative spend; control vs mart candidate totals),
   then replaces the `batch_id` slice atomically with `INSERT INTO ... REPLACE WHERE`.
   Business columns: active `CARD` debits in the window, inner join active `silver.dim_merchant`,
   `coalesce(category, 'UNCLASSIFIED')`, `count(transaction_id)`, `sum(-amount)`. Unlisted
   columns take the legacy DDL defaults as literals.
2. `verify_mart_card_spend_category` (`self_check.sql`): category grain, default columns and
   published totals vs the control aggregate.
