from pathlib import Path

import duckdb
import pytest

from migration_ci.model import statements


@pytest.fixture
def db():
    with duckdb.connect() as connection:
        connection.execute('CREATE SCHEMA bronze; CREATE SCHEMA silver; CREATE SCHEMA gold')
        connection.execute('CREATE TABLE bronze.dim_branch (branch_id INT)')
        connection.execute('CREATE TABLE silver.dim_customer (customer_id INT, branch_id INT, record_status_cd VARCHAR)')
        connection.execute('CREATE TABLE silver.dim_account (account_id INT, customer_id INT, record_status_cd VARCHAR)')
        connection.execute('''CREATE TABLE silver.fct_transaction (
            account_id INT, txn_date DATE, amount DECIMAL(18,2), record_status_cd VARCHAR)''')
        yield connection


def run(db):
    for name in ('01_validate.sql', '02_branch_league.sql', '03_reconcile.sql'):
        for statement in statements(Path(__file__).with_name(name).read_text()):
            db.execute(statement.sql(dialect='duckdb'))


def test_totals_ties_and_defaults(db):
    db.execute('INSERT INTO bronze.dim_branch VALUES (1), (2), (3), (4)')
    db.execute("INSERT INTO silver.dim_customer VALUES (1,1,'a '), (2,1,'A'), (3,2,'A'), (4,3,'A'), (5,4,'A')")
    db.execute("INSERT INTO silver.dim_account VALUES (1,1,'a '), (2,1,'A'), (3,3,'A'), (4,4,'D')")
    db.execute("""INSERT INTO silver.fct_transaction VALUES
        (1,'2024-01-01',-10,'a '), (2,'2025-06-30',5,'A'),
        (3,'2025-06-30',-5,'A'), (4,'2025-06-30',100,'A'),
        (1,'2023-12-31',100,'A'), (1,'2025-07-01',100,'A'),
        (1,'2025-06-30',100,'D')""")
    run(db)
    assert db.execute('''SELECT branch_id, customer_count, net_amount, league_rank
        FROM gold.mart_branch_performance ORDER BY branch_id''').fetchall() == [
        (1, 2, -5, 3), (2, 1, -5, 3), (3, 1, 0, 1), (4, 1, 0, 1)
    ]
    assert db.execute('''SELECT batch_id, src_system_cd, record_status_cd,
        strftime(load_ts, '%Y-%m-%d %H:%M:%S'), CAST(effective_date AS VARCHAR), CAST(expiry_date AS VARCHAR)
        FROM gold.mart_branch_performance WHERE branch_id=1''').fetchone() == (
        20250630, 'COREBANK', 'A', '2025-06-30 23:00:00', '2024-01-01', '9999-12-31'
    )


def test_empty(db):
    run(db)
    assert db.execute('SELECT COUNT(*) FROM gold.mart_branch_performance').fetchone() == (0,)


def test_unknown_branch(db):
    db.execute("INSERT INTO silver.dim_customer VALUES (1,99,'A')")
    with pytest.raises(duckdb.ConversionException, match='Branch league customer has no branch'):
        run(db)


def test_customer_control(db):
    db.execute('INSERT INTO bronze.dim_branch VALUES (1)')
    db.execute("INSERT INTO silver.dim_customer VALUES (1,1,'A'), (2,1,'D')")
    with pytest.raises(duckdb.ConversionException, match='Branch league reconciliation failed'):
        run(db)
