"""Reconcile gold business views and reports with the legacy view/report SQL run over the migrated marts."""
import json
import re
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
OBJECTS = ('v_customer_portfolio', 'v_credit_risk', 'v_channel_trend', 'report_finance_monthly',
           'report_branch_league', 'report_card_services', 'report_risk_arrears')


def quote(*parts):
    for part in parts:
        if not isinstance(part, str) or not re.fullmatch(r'[A-Za-z0-9_]+', part):
            raise ValueError(f'invalid identifier: {part!r}')
    return '.'.join(f'`{part}`' for part in parts)


def date_literal(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(f'invalid date: {value!r}')
    date.fromisoformat(value)
    return f"DATE '{value}'"


def year_literal(value):
    if not re.fullmatch(r'\d{4}', str(value)):
        raise ValueError(f'invalid year: {value!r}')
    return str(int(value))


def legacy_sql(catalog, silver_schema, gold_schema, as_of_date, window_start, report_year):
    """Legacy legacy/views/*.sql and reports/*.sql text translated to Databricks SQL (ORDER BY is presentation only)."""
    s = lambda table: quote(catalog, silver_schema, table)
    g = lambda table: quote(catalog, gold_schema, table)
    start, end, year = date_literal(window_start), date_literal(as_of_date), year_literal(report_year)
    credit_risk = f"""
SELECT a.customer_id, c.customer_name, b.town AS branch_town,
       COUNT(l.loan_id) AS loan_count,
       SUM(l.outstanding) AS exposure,
       MAX(l.arrears_days) AS worst_arrears_days,
       SUM(CASE WHEN l.arrears_days >= 90 THEN l.outstanding ELSE 0 END)
           / nullif(SUM(l.outstanding), 0) AS impaired_ratio,
       SUM(CASE WHEN l.arrears_days BETWEEN 30 AND 89 THEN l.outstanding ELSE 0 END) AS watchlist_exposure,
       CASE WHEN MAX(l.arrears_days) >= 90 THEN 'IMPAIRED'
            WHEN MAX(l.arrears_days) >= 30 THEN 'WATCH'
            ELSE 'PERFORMING' END AS risk_band
FROM {s('fct_loan')} AS l
INNER JOIN {s('dim_account')} AS a ON l.account_id = a.account_id
INNER JOIN {s('dim_customer')} AS c ON a.customer_id = c.customer_id
INNER JOIN {s('dim_branch')} AS b ON c.branch_id = b.branch_id
WHERE l.record_status_cd = 'A'
GROUP BY 1, 2, 3"""
    return {
        'v_customer_portfolio': f"""
SELECT c.customer_id, c.customer_name, c.town, b.branch_name, b.region_cd, s.segment,
       COUNT(a.account_id) AS account_count,
       SUM(CASE WHEN a.dormant_flag = 'Y' THEN 1 ELSE 0 END) AS dormant_account_count,
       COUNT(DISTINCT a.product_id) AS product_count,
       coalesce(MAX(s.spend), 0) AS trailing_year_debit_spend,
       MAX(c.load_ts) AS customer_delivery_ts
FROM {s('dim_customer')} AS c
INNER JOIN {s('dim_branch')} AS b ON c.branch_id = b.branch_id
LEFT JOIN {s('dim_account')} AS a ON c.customer_id = a.customer_id AND a.record_status_cd = 'A'
LEFT JOIN {g('mart_customer_segment')} AS s ON c.customer_id = s.customer_id
WHERE c.record_status_cd = 'A'
GROUP BY 1, 2, 3, 4, 5, 6""",
        'v_credit_risk': credit_risk,
        'v_channel_trend': f"""
SELECT d.calendar_date AS txn_date, d.year_no, d.month_no, d.month_start,
       'MOBILE' AS channel,
       coalesce(m.txn_count, 0) AS txn_count,
       coalesce(m.net_amount, 0) AS net_amount,
       SUM(coalesce(m.net_amount, 0)) OVER (ORDER BY d.calendar_date ROWS UNBOUNDED PRECEDING) AS running_amount,
       AVG(coalesce(m.net_amount, 0)) OVER (ORDER BY d.calendar_date ROWS 6 PRECEDING) AS seven_day_average,
       coalesce(m.net_amount, 0) / nullif(m.txn_count, 0) AS average_payment,
       CASE WHEN m.txn_count IS NULL THEN 'NO_ACTIVITY' ELSE 'CLEARED' END AS activity_status
FROM {s('dim_calendar')} AS d
LEFT JOIN {g('mart_channel_daily')} AS m ON d.calendar_date = m.txn_date AND m.channel = 'MOBILE'
WHERE d.calendar_date BETWEEN {start} AND {end}""",
        'report_branch_league': f"""
SELECT b.town, p.customer_count, p.net_amount, p.league_rank
FROM {g('mart_branch_performance')} AS p JOIN {s('dim_branch')} AS b ON p.branch_id = b.branch_id""",
        'report_card_services': f"""
SELECT category, txn_count, spend, spend / nullif(SUM(spend) OVER (), 0) AS share_of_spend
FROM {g('mart_card_spend_category')}""",
        'report_finance_monthly': f"""
SELECT month_start, SUM(closing_balance) AS closing_balance, SUM(average_balance) AS average_balance
FROM {g('mart_monthly_balance')}
WHERE EXTRACT(YEAR FROM month_start) = {year} GROUP BY 1""",
        'report_risk_arrears': f"""
SELECT customer_id, exposure, impaired_ratio FROM ({credit_risk}
) AS v_credit_risk
WHERE impaired_ratio > 0""",
    }


def multiset_sql(actual, expected):
    return f"""
WITH actual AS (SELECT * FROM {actual}), expected AS ({expected})
SELECT (SELECT COUNT(*) FROM actual), (SELECT COUNT(*) FROM expected),
       (SELECT COUNT(*) FROM (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)),
       (SELECT COUNT(*) FROM (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual))"""


def control_sql(catalog, silver_schema, gold_schema, as_of_date, window_start, report_year):
    """Legacy control invariants (target model 6.2.3) as (name, expected SQL, actual SQL) scalar pairs."""
    s = lambda table: quote(catalog, silver_schema, table)
    g = lambda table: quote(catalog, gold_schema, table)
    start, end, year = date_literal(window_start), date_literal(as_of_date), year_literal(report_year)
    mobile = (f"FROM {g('mart_channel_daily')} WHERE channel = 'MOBILE' "
              f"AND txn_date BETWEEN {start} AND {end}")
    loans = (f"FROM {s('fct_loan')} AS l JOIN {s('dim_account')} AS a ON l.account_id = a.account_id "
             f"JOIN {s('dim_customer')} AS c ON a.customer_id = c.customer_id "
             f"JOIN {s('dim_branch')} AS b ON c.branch_id = b.branch_id WHERE l.record_status_cd = 'A'")
    customers = (f"FROM {s('dim_customer')} AS c JOIN {s('dim_branch')} AS b ON c.branch_id = b.branch_id "
                 f"WHERE c.record_status_cd = 'A'")
    return [
        ('v_channel_trend calendar spine (days, first, last)',
         f"SELECT concat_ws('|', CAST(COUNT(*) AS STRING), CAST(MIN(calendar_date) AS STRING), CAST(MAX(calendar_date) AS STRING)) FROM {s('dim_calendar')} "
         f"WHERE calendar_date BETWEEN {start} AND {end}",
         f"SELECT concat_ws('|', CAST(COUNT(*) AS STRING), CAST(MIN(txn_date) AS STRING), CAST(MAX(txn_date) AS STRING)) FROM {g('v_channel_trend')}"),
        ('v_channel_trend MOBILE totals (txn_count, net_amount)',
         f"SELECT concat_ws('|', CAST(coalesce(SUM(txn_count), 0) AS STRING), CAST(coalesce(SUM(net_amount), 0) AS STRING)) {mobile}",
         f"SELECT concat_ws('|', CAST(SUM(txn_count) AS STRING), CAST(SUM(net_amount) AS STRING)) FROM {g('v_channel_trend')}"),
        ('v_channel_trend final running_amount = MOBILE net_amount',
         f"SELECT CAST(coalesce(SUM(net_amount), 0) AS STRING) {mobile}",
         f"SELECT CAST(MAX_BY(running_amount, txn_date) AS STRING) FROM {g('v_channel_trend')}"),
        ('v_credit_risk active loans (loans, exposure)',
         f"SELECT concat_ws('|', CAST(COUNT(l.loan_id) AS STRING), CAST(SUM(l.outstanding) AS STRING)) {loans}",
         f"SELECT concat_ws('|', CAST(SUM(loan_count) AS STRING), CAST(SUM(exposure) AS STRING)) FROM {g('v_credit_risk')}"),
        ('v_customer_portfolio active customers',
         f"SELECT CAST(COUNT(DISTINCT c.customer_id) AS STRING) {customers}",
         f"SELECT CAST(COUNT(*) AS STRING) FROM {g('v_customer_portfolio')}"),
        ('report_finance_monthly year totals (closing, average)',
         f"SELECT concat_ws('|', CAST(SUM(closing_balance) AS STRING), CAST(SUM(average_balance) AS STRING)) FROM {g('mart_monthly_balance')} "
         f"WHERE EXTRACT(YEAR FROM month_start) = {year}",
         f"SELECT concat_ws('|', CAST(SUM(closing_balance) AS STRING), CAST(SUM(average_balance) AS STRING)) FROM {g('report_finance_monthly')}"),
    ]


def run(query, schema, catalog, silver_schema, gold_schema, drop, manifest_drops,
        as_of_date, window_start, report_year, expectations=None):
    """Return check rows; `query(sql)` returns row tuples, `schema(sql)` returns (name, type) pairs."""
    args = (catalog, silver_schema, gold_schema, as_of_date, window_start, report_year)
    results = []

    def record(check, expected, actual):
        results.append(dict(check=check, expected=str(expected), actual=str(actual),
                            status='PASS' if str(expected) == str(actual) else 'FAIL'))

    delivery = json.loads(manifest_drops).get(drop)
    if delivery is None:
        raise ValueError(f'Unknown captured drop: {drop}')
    record('as_of_date = manifest business_date', delivery['business_date'], as_of_date)
    for name, expected in legacy_sql(*args).items():
        actual = quote(catalog, gold_schema, name)
        expected_columns = schema(f'SELECT * FROM ({expected}) LIMIT 0')
        actual_columns = schema(f'SELECT * FROM {actual} LIMIT 0')
        record(f'{name} columns', ', '.join(f'{n} {t}' for n, t in expected_columns),
               ', '.join(f'{n} {t}' for n, t in actual_columns))
        actual_rows, expected_rows, extra, missing = map(int, query(multiset_sql(actual, expected))[0])
        results.append(dict(check=f'{name} multiset vs legacy SQL', expected=f'{expected_rows} rows',
                            actual=f'{actual_rows} rows, {extra} unexpected, {missing} missing',
                            status='PASS' if (actual_rows, missing, extra) == (expected_rows, 0, 0) else 'FAIL'))
    for check, expected, actual in control_sql(*args):
        record(check, query(expected)[0][0], query(actual)[0][0])
    expectations = expectations if expectations is not None else load_expectations(drop)
    if expectations:
        for name, rows in expectations['rows'].items():
            record(f'{name} {drop} rows (target model 6.1)', rows,
                   query(f'SELECT COUNT(*) FROM {quote(catalog, gold_schema, name)}')[0][0])
        bands = dict(query(f"SELECT risk_band, COUNT(*) FROM {quote(catalog, gold_schema, 'v_credit_risk')} "
                           'GROUP BY risk_band'))
        for band, count in expectations['v_credit_risk_bands'].items():
            record(f'v_credit_risk {band} customers {drop} (target model 6.1)', count, bands.get(band, 0))
    return results


def load_expectations(drop):
    path = HERE / f'expected_{drop}.json'
    return json.loads(path.read_text()) if path.exists() else None


def markdown(results):
    lines = ['| check | expected | actual | status |', '| --- | --- | --- | --- |']
    lines += [f"| {r['check']} | {r['expected']} | {r['actual']} | {r['status']} |" for r in results]
    failed = sum(r['status'] != 'PASS' for r in results)
    lines.append(f'\n{len(results) - failed}/{len(results)} checks PASS')
    return '\n'.join(lines)
