import calendar
import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
SQL = (HERE / 'customer_segment.sql').read_text()
AS_OF = '2025-06-30'


def add_months(value, months):
    value = date.fromisoformat(value)
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1])).isoformat()


def shift(value, days):
    return (date.fromisoformat(value) + timedelta(days=days)).isoformat()


def view(name):
    body = SQL.split(f'TEMPORARY VIEW {name} AS', 1)[1].split(';', 1)[0]
    body = re.sub(r'CAST\((.*?) AS (?:INT|DECIMAL\(18,2\))\)', r'(\1)', body)
    return body.replace('v_as_of_date', f"'{AS_OF}'")


def segments(customers, accounts, transactions):
    db = sqlite3.connect(':memory:')
    db.create_function('add_months', 2, add_months)
    db.create_function('date_add', 2, shift)
    db.create_function('date_sub', 2, lambda v, d: shift(v, -d))
    db.executescript('''
      CREATE TABLE dim_customer(customer_id INT, record_status_cd TEXT);
      CREATE TABLE dim_account(account_id INT, customer_id INT, record_status_cd TEXT);
      CREATE TABLE fct_transaction(transaction_id INT, account_id INT, txn_date TEXT,
                                   amount NUMERIC, record_status_cd TEXT);''')
    db.executemany('INSERT INTO dim_customer VALUES (?, ?)', customers)
    db.executemany('INSERT INTO dim_account VALUES (?, ?, ?)', accounts)
    db.executemany('INSERT INTO fct_transaction VALUES (?, ?, ?, ?, ?)', transactions)
    rows = db.execute(view('vt_customer_segment')).fetchall()
    return {row[0]: row[1:] for row in rows}


def txns(account, count, amount, last='2025-06-01', start=0):
    return [(start + i, account, last, amount, 'A') for i in range(count)]


def test_segment_rules_first_match_wins():
    customers = [(c, 'A') for c in range(1, 7)] + [(7, 'D')]
    accounts = [(c, c, 'A') for c in range(1, 8)] + [(60, 6, 'D')]
    transactions = (
        txns(1, 60, -400, start=1000)                      # PREMIUM: 60 debits, 24000 spend
        + txns(2, 60, -100, start=2000)                    # ACTIVE: spend below 20000
        + txns(3, 5, -10, start=3000)                      # OCCASIONAL
        + txns(4, 60, -400, last='2025-03-31', start=4000)  # DORMANT: last before 2025-04-01
        + txns(5, 1, 50, last='2025-04-01', start=5000)     # OCCASIONAL: 2025-04-01 is not dormant
        + txns(60, 30, -10, start=6000)                    # inactive account ignored
        + [(7000, 1, '2024-06-30', -5, 'A'),                # outside window
           (7001, 1, '2024-07-01', -5, 'A'),                # first window day
           (7002, 1, '2025-06-29', -999, 'D')])             # inactive transaction
    result = segments(customers, accounts, transactions)
    assert set(result) == {1, 2, 3, 4, 5, 6}
    assert result[1][0] == 61 and result[1][1] == 24005 and result[1][3] == 'PREMIUM'
    assert result[2][3] == 'ACTIVE'
    assert result[3][3] == 'OCCASIONAL'
    assert result[4][3] == 'DORMANT'
    assert result[5] == (1, 0, '2025-04-01', 'OCCASIONAL')
    assert result[6] == (0, 0, None, 'DORMANT')


def test_window_is_add_months_plus_one_day():
    assert shift(add_months(AS_OF, -12), 1) == '2024-07-01'
    assert shift(AS_OF, -90) == '2025-04-01'


def test_controls_raise_before_write():
    write = SQL.index('CREATE TABLE IF NOT EXISTS')
    for message in ('Segmentation requires a business date', 'customer_id <= 0',
                    'Customer segment coverage failed', 'missing or invalid segment rows'):
        assert SQL.index(message) < write
    assert SQL.index('DELETE FROM') > write and SQL.index('INSERT INTO') > SQL.index('DELETE FROM')


def test_unit_and_job_contract():
    unit = json.loads((HERE / 'unit.json').read_text())
    assert unit == {'job': 'gold_customer_segment', 'legacy_unit': 'customer_segment',
                    'outputs': ['gold.mart_customer_segment'], 'owns': [],
                    'depends_on': ['silver_customer', 'silver_dormant', 'silver_transactions']}
    job = yaml.safe_load((HERE / 'gold_customer_segment.job.yml').read_text())['resources']['jobs'][unit['job']]
    assert job['name'] == '${var.job_name_prefix}gold_customer_segment'
    defaults = {p['name']: p['default'] for p in job['parameters']}
    assert defaults == {'drop': 'D0', 'as_of_date': '2025-06-30', 'batch_id': '20250630',
                        'load_ts': '2025-06-30 23:00:00'}
    tasks = {t['task_key']: t for t in job['tasks']}
    assert tasks['verify_mart_customer_segment']['depends_on'] == [{'task_key': 'build_mart_customer_segment'}]
    for task in tasks.values():
        assert task['sql_task']['parameters']['catalog'] == '${var.catalog}'
        assert (HERE / task['sql_task']['file']['path']).exists()


@pytest.mark.parametrize('column', ['customer_id', 'txn_count', 'spend', 'last_txn_date', 'segment',
                                    'batch_id', 'load_ts', 'src_system_cd', 'record_status_cd',
                                    'effective_date', 'expiry_date'])
def test_ddl_column_order(column):
    ddl = SQL.split('CREATE TABLE IF NOT EXISTS', 1)[1].split('TBLPROPERTIES', 1)[0]
    names = re.findall(r'^\s+([a-z_]+) ', ddl, re.M)
    assert names[['customer_id', 'txn_count', 'spend', 'last_txn_date', 'segment', 'batch_id', 'load_ts',
                  'src_system_cd', 'record_status_cd', 'effective_date', 'expiry_date'].index(column)] == column
