from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from migration_ci.model import statements


def execute(db, files=None):
    directory = Path(__file__).parent
    for file in files or ['01_validate.sql', '02_monthly_balance.sql', '03_reconcile.sql']:
        for statement in statements((directory / file).read_text()):
            db.execute(statement.sql(dialect='duckdb'))


@pytest.fixture
def db():
    with duckdb.connect() as connection:
        for schema in ['bronze', 'silver', 'gold']:
            connection.execute(f'CREATE SCHEMA {schema}')
        connection.execute('CREATE TABLE silver.dim_account (account_id INT, record_status_cd VARCHAR)')
        connection.execute("INSERT INTO silver.dim_account VALUES (1, 'A'), (2, 'a '), (3, 'A')")
        connection.execute('''
            CREATE TABLE bronze.fct_daily_balance AS
            SELECT CAST(account_id AS INT) AS account_id, CAST(d AS DATE) AS balance_date,
                   CAST(CASE WHEN account_id = 3 THEN
                       CASE WHEN day(d) = 30 THEN 2 ELSE 1000 END
                       ELSE (CASE WHEN day(d) <= 15 THEN 1.00 ELSE 1.01 END)
                            * (CASE WHEN account_id = 2 THEN -1 ELSE 1 END)
                   END AS DECIMAL(18,2)) AS balance,
                   'a ' AS record_status_cd
            FROM generate_series(DATE '2025-06-01', DATE '2025-06-30', INTERVAL 1 DAY) dates(d)
            CROSS JOIN (VALUES (1), (2), (3)) accounts(account_id)
        ''')
        yield connection


def test_half_up_rounding_month_end_and_restart(db):
    execute(db)
    execute(db)
    assert db.execute('''
        SELECT account_id, average_balance, closing_balance
        FROM gold.mart_monthly_balance ORDER BY account_id
    ''').fetchall() == [
        (1, Decimal('1.01'), Decimal('1.01')),
        (2, Decimal('-1.01'), Decimal('-1.01')),
        (3, Decimal('966.73'), Decimal('2.00')),
    ]
    assert db.execute('''
        SELECT CAST(month_start AS VARCHAR), batch_id, CAST(load_ts AS VARCHAR),
               src_system_cd, record_status_cd, CAST(effective_date AS VARCHAR),
               CAST(expiry_date AS VARCHAR)
        FROM gold.mart_monthly_balance WHERE account_id = 1
    ''').fetchone() == (
        '2025-06-01', 20250630, '2025-06-30 23:00:00+00', 'COREBANK', 'A',
        '2024-01-01', '9999-12-31',
    )
    types = dict((row[0], row[1]) for row in db.execute('DESCRIBE gold.mart_monthly_balance').fetchall())
    assert types['month_start'] == 'DATE'
    assert types['average_balance'] == types['closing_balance'] == 'DECIMAL(18,2)'


@pytest.mark.parametrize('mutation, message', [
    ("DELETE FROM bronze.fct_daily_balance WHERE balance_date = DATE '2025-06-15'", 'Incomplete'),
    ("DELETE FROM bronze.fct_daily_balance WHERE balance_date = DATE '2025-06-30'", 'Incomplete'),
    ("UPDATE bronze.fct_daily_balance SET balance_date = DATE '2025-06-14' WHERE balance_date = DATE '2025-06-15'", 'Incomplete'),
    ('DELETE FROM silver.dim_account WHERE account_id = 1', 'Unmatched'),
    ("UPDATE bronze.fct_daily_balance SET balance = NULL WHERE balance_date = DATE '2025-06-30'", 'reconciliation'),
])
def test_rejects_failed_controls(db, mutation, message):
    db.execute(mutation)
    with pytest.raises(duckdb.ConversionException, match=message):
        execute(db)


def test_leap_month_and_lower_window_boundary(db):
    db.execute('DELETE FROM bronze.fct_daily_balance')
    db.execute('''
        INSERT INTO bronze.fct_daily_balance
        SELECT 1, CAST(d AS DATE), 10.00, 'A'
        FROM generate_series(DATE '2024-01-01', DATE '2024-02-29', INTERVAL 1 DAY) dates(d)
    ''')
    execute(db)
    assert db.execute('''
        SELECT CAST(month_start AS VARCHAR), average_balance, closing_balance
        FROM gold.mart_monthly_balance ORDER BY month_start
    ''').fetchall() == [
        ('2024-01-01', Decimal('10.00'), Decimal('10.00')),
        ('2024-02-01', Decimal('10.00'), Decimal('10.00')),
    ]


def test_active_filters_and_inclusive_window(db):
    db.execute("UPDATE silver.dim_account SET record_status_cd = 'D' WHERE account_id = 2")
    db.execute("UPDATE bronze.fct_daily_balance SET record_status_cd = 'D' WHERE account_id = 3")
    db.execute('''
        INSERT INTO bronze.fct_daily_balance VALUES
        (1, DATE '2023-12-31', 99999, 'A'), (1, DATE '2025-07-01', 99999, 'A')
    ''')
    execute(db, ['02_monthly_balance.sql'])
    assert db.execute('''
        SELECT account_id, average_balance, closing_balance FROM gold.mart_monthly_balance
    ''').fetchall() == [(1, Decimal('1.01'), Decimal('1.01'))]
