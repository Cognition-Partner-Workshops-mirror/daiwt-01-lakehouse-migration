from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from migration_ci.model import statements
from sqlglot import exp


@pytest.fixture
def db():
    with duckdb.connect() as connection:
        connection.execute("CREATE SCHEMA bronze; CREATE SCHEMA silver; CREATE SCHEMA gold")
        connection.execute("""
            CREATE TABLE bronze.fct_daily_balance (
                account_id INT, balance DECIMAL(18,2), balance_date DATE,
                record_status_cd VARCHAR);
            CREATE TABLE silver.dim_account (
                account_id INT, product_id INT, record_status_cd VARCHAR);
            CREATE TABLE bronze.dim_product (product_id INT, annual_rate DECIMAL(9,4));
            INSERT INTO silver.dim_account VALUES (1, 10, 'A '), (2, 10, 'A'), (3, 10, 'D');
            INSERT INTO bronze.dim_product VALUES (10, 2.5);
        """)
        yield connection


def run(db):
    for file in sorted(Path(__file__).parent.glob('*.sql')):
        for statement in statements(file.read_text()):
            if isinstance(statement, exp.Create):
                statement.set('properties', None)
            db.execute(statement.sql(dialect='duckdb'))
    return db.execute(
        'SELECT product_id, average_balance, annual_income FROM gold.mart_product_profitability'
    ).fetchall()


def test_weighted_mean_window_and_status(db):
    db.execute("""
        INSERT INTO bronze.fct_daily_balance VALUES
            (1, 100, '2024-01-01', 'A '),
            (1, 100, '2025-06-29', 'A'),
            (2, 200.02, '2025-06-30', 'A'),
            (1, 9999, '2023-12-31', 'A'),
            (1, 9999, '2025-07-01', 'A'),
            (1, 9999, '2025-06-30', 'D'),
            (3, 9999, '2025-06-30', 'A');
    """)
    assert run(db) == [(10, Decimal('133.34'), Decimal('3.33'))]


def test_empty(db):
    assert run(db) == []


@pytest.mark.parametrize('rate,balance', [('NULL', '100'), ('-1', '100'), ('2.5', 'NULL')])
def test_invalid_aggregate(db, rate, balance):
    db.execute(f'UPDATE bronze.dim_product SET annual_rate = {rate}')
    db.execute(f"INSERT INTO bronze.fct_daily_balance VALUES (1, {balance}, '2025-06-30', 'A')")
    with pytest.raises(duckdb.Error, match='Invalid product rate or mean balance'):
        run(db)
