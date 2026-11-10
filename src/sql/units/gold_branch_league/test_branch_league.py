import json
import re
import sqlite3
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
SQL = (HERE / 'branch_league.sql').read_text()
PARAMS = {'as_of_date': '2025-06-30', 'window_start': '2024-01-01', 'batch_id': '20250630'}


def to_sqlite(sql, params):
    sql = re.sub(r"IDENTIFIER\(:catalog \|\| '\.' \|\| :\w+ \|\| '\.(\w+)'\)", r'\1', sql)
    sql = re.sub(r':(as_of_date|window_start|batch_id)\b', lambda m: f"'{params[m.group(1)]}'", sql)
    sql = re.sub(r'CAST\((.*?) AS (DATE|INT|BIGINT|DECIMAL\(18,2\)|TIMESTAMP_NTZ)\)', r'\1', sql)
    sql = sql.replace('LEFT ANTI JOIN', 'LEFT JOIN')
    sql = sql.replace("DATE '", "'").replace('count_if(', 'sum(')
    return sql


def statements(params=PARAMS):
    parts = [s.strip() for s in to_sqlite(SQL, params).split(';') if s.strip()]
    ctas = parts.pop()
    select = 'WITH' + ctas.split('AS\nWITH', 1)[1]
    return parts, select


def database(customers, accounts, transactions, branches=(1, 2, 3)):
    db = sqlite3.connect(':memory:')
    db.create_function('raise_error', 1, lambda message: (_ for _ in ()).throw(ValueError(message)))
    db.create_function('concat', 2, lambda a, b: a + b)
    db.executescript('''
      CREATE TABLE dim_customer(customer_id INT, branch_id INT, record_status_cd TEXT);
      CREATE TABLE dim_branch(branch_id INT);
      CREATE TABLE dim_account(account_id INT, customer_id INT, record_status_cd TEXT);
      CREATE TABLE fct_transaction(account_id INT, txn_date TEXT, amount NUMERIC, record_status_cd TEXT);
    ''')
    db.executemany('INSERT INTO dim_customer VALUES (?,?,?)', customers)
    db.executemany('INSERT INTO dim_branch VALUES (?)', [(b,) for b in branches])
    db.executemany('INSERT INTO dim_account VALUES (?,?,?)', accounts)
    db.executemany('INSERT INTO fct_transaction VALUES (?,?,?,?)', transactions)
    return db


def run(db, params=PARAMS):
    checks, select = statements(params)
    checks[1] = checks[1].replace('LEFT JOIN dim_branch AS b\n  ON c.branch_id = b.branch_id',
                                  'LEFT JOIN dim_branch AS b ON c.branch_id = b.branch_id WHERE b.branch_id IS NULL')
    for check in checks:
        db.execute(check).fetchall()
    return sorted(db.execute(select).fetchall())


BASE_CUSTOMERS = [(1, 1, 'A'), (2, 1, 'A'), (3, 2, 'A'), (4, 3, 'A')]
BASE_ACCOUNTS = [(10, 1, 'A'), (11, 2, 'A'), (12, 2, 'D'), (30, 3, 'A'), (40, 4, 'A')]


def test_signed_net_rank_and_defaults():
    rows = run(database(BASE_CUSTOMERS, BASE_ACCOUNTS, [
        (10, '2024-01-01', 100.00, 'A'), (11, '2025-06-30', -40.00, 'A'),
        (12, '2025-01-01', 999.00, 'A'),   # inactive account
        (10, '2023-12-31', 500.00, 'A'),   # before window
        (10, '2025-07-01', 500.00, 'A'),   # after as_of
        (30, '2024-06-01', 75.00, 'D'),    # inactive transaction
        (40, '2024-06-01', -5.00, 'A')]))
    assert [r[:4] for r in rows] == [(1, 2, 60.0, 1), (2, 1, 0, 2), (3, 1, -5.0, 3)]
    assert {r[4:] for r in rows} == {('20250630', '2025-06-30 23:00:00', 'COREBANK', 'A', '2024-01-01', '9999-12-31')}


def test_ties_share_rank():
    rows = run(database(BASE_CUSTOMERS, BASE_ACCOUNTS, [(10, '2024-02-01', 10, 'A'), (30, '2024-02-01', 10, 'A')]))
    assert [r[3] for r in rows] == [1, 1, 3]


def test_window_follows_parameters():
    params = dict(PARAMS, window_start='2025-01-01')
    rows = run(database(BASE_CUSTOMERS, BASE_ACCOUNTS, [(10, '2024-02-01', 10, 'A'), (10, '2025-02-01', 7, 'A')]), params)
    assert rows[0][2] == 7


def test_missing_branch_rejected():
    with pytest.raises(sqlite3.OperationalError, match='user-defined function raised exception'):
        run(database(BASE_CUSTOMERS + [(5, 9, 'A')], BASE_ACCOUNTS, []))


def test_inactive_customer_fails_control():
    with pytest.raises(sqlite3.OperationalError, match='user-defined function raised exception'):
        run(database(BASE_CUSTOMERS + [(5, 1, 'D')], BASE_ACCOUNTS, []))


def test_registry_and_job():
    unit = json.loads((HERE / 'unit.json').read_text())
    assert list(unit) == ['job', 'legacy_unit', 'outputs', 'owns', 'depends_on']
    job = yaml.safe_load((HERE / 'gold_branch_league.job.yml').read_text())['resources']['jobs'][unit['job']]
    assert job['name'].startswith('${var.job_name_prefix}')
    defaults = {p['name']: p['default'] for p in job['parameters']}
    assert defaults == {'drop': 'D0', **PARAMS}
    assert [t['task_key'] for t in job['tasks']] == ['build_mart_branch_performance', 'verify_mart_branch_performance']
    for task in job['tasks']:
        assert task['sql_task']['parameters']['catalog'] == '${var.catalog}'
