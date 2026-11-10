import calendar
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest


SQL = Path(__file__).with_name('dormant.pipeline.sql').read_text()


def add_months(value, months):
    value = date.fromisoformat(value)
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1])).isoformat()


def query(name):
    statement = next(s for s in SQL.split(';') if f'MATERIALIZED VIEW {name}\n' in s)
    return ('SELECT' + statement.split('AS SELECT', 1)[1]).replace('AS STRING', 'AS TEXT')


def database(batch=20250630):
    db = sqlite3.connect(':memory:')
    db.create_function('add_months', 2, add_months)
    db.create_function('to_date', 2, lambda v, f: datetime.strptime(v, '%Y%m%d').date().isoformat())
    db.create_function('date_format', 2, lambda v, f: v.replace('-', ''))
    db.create_function('raise_error', 1, lambda message: (_ for _ in ()).throw(ValueError(message)))
    db.executescript('''
      CREATE TABLE dormant_accounts(account_id INT, customer_id INT, product_id INT,
        opened_date TEXT, dormant_flag TEXT, batch_id INT, load_ts TEXT,
        src_system_cd TEXT, record_status_cd TEXT, effective_date TEXT,
        expiry_date TEXT, currency_cd TEXT, account_status_cd TEXT,
        accrual_basis_cd TEXT, overdraft_limit NUMERIC);
      CREATE TABLE dim_customer(customer_id INT, branch_id INT);
      CREATE TABLE dim_branch(branch_id INT, record_status_cd TEXT);
      CREATE TABLE fct_transaction(account_id INT, txn_date TEXT, record_status_cd TEXT);
      INSERT INTO dim_customer VALUES (1,1);
      INSERT INTO dim_branch VALUES (1,'A');
    ''')
    db.execute("INSERT INTO dormant_accounts VALUES (1,1,2,'2024-01-01','Y',?,NULL,'COREBANK','A','2024-01-01','9999-12-31','NOK','OPEN','ACT365',0)", (batch,))
    return db


def evaluate(db):
    for name in ['dormant_run_params', 'dormant_customer_control']:
        db.execute(f'CREATE TABLE {name} AS ' + query(name))
    return db.execute(query('dormant_candidates')).fetchall()


@pytest.mark.parametrize('txn,expected', [
    ('2024-12-29','Y'), ('2024-12-30','Y'), ('2024-12-31','N'),
    ('2025-06-30','N'), ('2025-07-01','Y'),
])
def test_window(txn, expected):
    db = database()
    db.execute("INSERT INTO fct_transaction VALUES (1,?,'A')", (txn,))
    assert evaluate(db)[0][4] == expected


@pytest.mark.parametrize('txn,expected', [('2025-02-28','Y'), ('2025-03-01','N')])
def test_month_end(txn, expected):
    db = database(20250831)
    db.execute("INSERT INTO fct_transaction VALUES (1,?,'A')", (txn,))
    assert evaluate(db)[0][4] == expected


def test_no_transactions():
    assert evaluate(database())[0][4] == 'Y'


def test_not_in_null():
    db = database()
    db.execute("INSERT INTO fct_transaction VALUES (NULL,'2025-01-01','A')")
    assert evaluate(db)[0][4] == 'N'


def test_inactive_transaction():
    db = database()
    db.execute("INSERT INTO fct_transaction VALUES (1,'2025-01-01','D')")
    assert evaluate(db)[0][4] == 'Y'


@pytest.mark.parametrize('change,expected', [
    ("UPDATE dim_branch SET record_status_cd='D'", 'N'),
    ('DELETE FROM dim_branch', 'N'),
    ("UPDATE dormant_accounts SET record_status_cd='D'", 'Y'),
])
def test_cursor_scope(change, expected):
    db = database()
    db.execute(change)
    assert evaluate(db)[0][4] == expected


def test_missing_customer_rejects_all_accounts():
    db = database()
    db.execute('DELETE FROM dim_customer')
    with pytest.raises(sqlite3.OperationalError, match='user-defined function'):
        evaluate(db)


@pytest.mark.parametrize('change', [
    'DELETE FROM dormant_accounts',
    'UPDATE dormant_accounts SET batch_id=NULL',
    'UPDATE dormant_accounts SET batch_id=20250230',
    'INSERT INTO dormant_accounts(account_id,batch_id) VALUES (2,20250701)',
])
def test_invalid_delivery_date(change):
    db = database()
    db.execute(change)
    with pytest.raises(sqlite3.OperationalError, match='user-defined function'):
        evaluate(db)


def test_contract_and_quarantine():
    assert 'TEMPORARY VIEW' not in SQL
    assert 'NOT IN' in SQL
    assert "WHERE account_id IS NULL" in SQL
    assert 'ON VIOLATION DROP ROW' in SQL
    for statement in SQL.split(';'):
        if any(name in statement for name in ['VIEW dormant_accounts\n', 'VIEW dormant_candidates\n', 'VIEW dim_account (', 'VIEW dim_account_quarantine\n']):
            assert "'delta.feature.timestampNtz'='supported'" in statement
    db = database()
    db.execute('UPDATE dormant_accounts SET account_id=NULL')
    rows = evaluate(db)
    assert len(rows) == 1 and rows[0][0] is None
    assert re.search(r'overdraft_limit DECIMAL\(18,2\)', SQL)
