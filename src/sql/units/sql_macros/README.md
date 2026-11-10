# Teradata compatibility SQL helpers

The sql_macros job installs deterministic SQL UDFs in the silver schema, then
runs self_check.sql on the same warehouse in a dependent task. These functions
are drop-independent; the job accepts the standard drop parameter. No tables or
golden outputs are owned. The registry legacy_unit customer links this shared
foundation to an existing source contract unit; it does not claim its outputs.

All legacy/bteq, legacy/macros, legacy/procs, legacy/views, legacy/ddl,
legacy/schedules and reports files were reviewed. Only semantics actually used
are included. Paths and line numbers below refer to the read-only Teradata repo.

| Function | Signature / return | Legacy motivation | Semantics |
| --- | --- | --- | --- |
| f_td_add_months | (d DATE, n INT) -> DATE | legacy/procs/flag_dormant.sql:40; legacy/procs/customer_segment.sql:44 | Teradata ADD_MONTHS retains day number when valid, otherwise clips to target month end. June 30 minus 6 months is December 30, not December 31. |
| f_month_start | (d DATE) -> DATE | legacy/macros/monthly_balance.sql:9,20; legacy/bteq/04_monthly_balance.btq:28 | TRUNC(date, MONTH), DATE result. |
| f_month_end | (d DATE) -> DATE | legacy/macros/monthly_balance.sql:23; legacy/bteq/04_monthly_balance.btq:36 | ADD_MONTHS(TRUNC(date, MONTH), 1) - 1; leap-aware month end. |
| f_decimal_2 | (v DECIMAL(38,6)) -> DECIMAL(18,2) | legacy/macros/monthly_balance.sql:21; legacy/bteq/07_product_profitability.btq:49-50 | Fixed 2-place HALF_UP, ties away from zero, no DOUBLE or banker rounding. For source DECIMAL(28,6) aggregates; ANSI overflow fails. |
| f_zeroifnull | (v DECIMAL(38,2)) -> DECIMAL(38,2) | legacy/macros/channel_daily.sql:22; legacy/procs/branch_league.sql:31-32; legacy/views/v_customer_portfolio.sql:12 | Money/count domain used here: NULL -> zero, otherwise unchanged. Input has at most two fractional digits; not a polymorphic replacement for arbitrary scales. |
| f_nullifzero | (v DECIMAL(38,2)) -> DECIMAL(38,2) | legacy/views/v_credit_risk.sql:10; legacy/bteq/08_channel.btq:49; reports/card_services.sql:2 | Money/count domain: zero -> NULL for safe division, NULL -> NULL, nonzero unchanged. Count values remain exact but result is DECIMAL, not BIGINT. |
| f_trim | (s STRING) -> STRING | legacy/bteq/01_customer.btq:29,31,33; legacy/bteq/02_transactions.btq:29-34; legacy/macros/channel_daily.sql:20 | Trim ASCII spaces, including delivered CHAR padding, not arbitrary Unicode whitespace. NULL -> NULL; empty -> empty. |
| f_clean_text | (s STRING) -> STRING | legacy/bteq/01_customer.btq:30,32 | OREPLACE NBSP with ASCII space then TRIM; preserve internal spaces and apostrophes. |
| f_parse_amount | (s STRING) -> DECIMAL(18,2) | legacy/bteq/02_transactions.btq:32 | TRYCAST decimal-comma raw delivery strings; invalid/overflow -> NULL for downstream rejection, not silent row filtering. |

## Exactness boundaries

There is no explicit ROUND call in the estate. f_decimal_2 covers its narrowing
DECIMAL casts and the captured controls' ROUND_HALF_UP convention instead; keep
AVG intermediates DECIMAL(28,6) as source and do not use floating point or bround.
The numeric NULL helpers cover the estate's cents and integral counts, not
arbitrary decimal scales. Prefer native coalesce/nullif when preserving an
expression's exact inferred precision or BIGINT type matters. Do not round an
impaired ratio to cents: apply the NULLIFZERO helper to the denominator only.

ADD_MONTHS is not Oracle-style end-of-month propagation (OADD_MONTHS is not used).
Spark add_months has the required day-preserving / short-month-clipping behavior.
The runbook legacy/schedules/runbook.md:48-50 and golden/compute_baselines.py:71
pin the inclusive first recent-payment date to 2024-12-31: June 30 minus six
months is December 30, and the source predicate is strictly greater than that
cutoff. Preserve that predicate. February 29 plus one month is March 29, not
March 31. These boundary cases prevent accidental Oracle semantics.

No day-of-week or custom date-format helper is added: none is used. Native
EXTRACT(YEAR FROM d) (reports/finance_monthly.sql:4), dayofmonth(d)
(legacy/bteq/04_monthly_balance.btq:37) and CAST(s AS DATE)
(legacy/bteq/02_transactions.btq:60) cover the source expressions; FORMAT on a
DATE cast does not turn it into a string. Date +/- integer translates to date_add
or date_sub. CSUM and MAVG (legacy/views/v_channel_trend.sql:10-11) require native
SUM/AVG windows with explicit ROWS BETWEEN UNBOUNDED PRECEDING / 6 PRECEDING AND
CURRENT ROW, not scalar UDFs. HASHROW is computed but unused downstream in
legacy/bteq/01_customer.btq:34; no incompatible Spark hash substitute is offered.
Source NOT CASESPECIFIC collation is not silently simulated by lowercasing raw
strings; consumers must preserve the specific source join/ordering semantics.

## Calling convention

Consumers declare depends_on: [sql_macros] and pass catalog/silver_schema to
their SQL-file tasks. Names are fully qualified and session-independent:

```sql
SELECT IDENTIFIER(:catalog || '.' || :silver_schema || '.f_td_add_months')(DATE '2025-06-30', -6);
```

Self-checks assert normal, NULL, empty, negative/tie, leap-year, end-of-month,
invalid and overflow cases as applicable using null-safe equality and raise_error.
Apostrophes are built with chr(39): Spark SQL adjacent quoted literals concatenate,
so doubled single quotes are not used as apostrophe escaping. These are offline
structural checks plus executable warehouse self-check SQL; no live SQL execution
or deployment has been performed in this step.
