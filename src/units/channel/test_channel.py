from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from migration_ci.model import statements


SQL = Path(__file__).with_name('01_channel.sql').read_text()


@pytest.fixture
def db():
    with duckdb.connect() as connection:
        connection.execute('CREATE SCHEMA bronze; CREATE SCHEMA silver; CREATE SCHEMA gold')
        connection.execute('CREATE TABLE bronze.dim_calendar (calendar_date DATE)')
        connection.execute("INSERT INTO bronze.dim_calendar VALUES ('2024-01-01'), ('2025-06-30')")
        connection.execute('''CREATE TABLE silver.fct_transaction (
            txn_date DATE, channel VARCHAR, amount DECIMAL(18,2), record_status_cd VARCHAR)''')
        yield connection


def run(db):
    for statement in statements(SQL):
        if isinstance(statement, exp.Create):
            statement.set('properties', None)
        db.execute(statement.sql(dialect='duckdb', unsupported_level=sqlglot.ErrorLevel.RAISE))


def test_groups_defaults_and_replacement(db):
    db.execute("""INSERT INTO silver.fct_transaction VALUES
        ('2024-01-01', ' Card ', -10.25, 'A '),
        ('2024-01-01', 'Card', 3.10, 'A'),
        ('2024-01-01', 'CARD', 1, 'A'),
        ('2025-06-30', '', NULL, 'A'),
        ('2025-06-30', NULL, NULL, 'A'),
        ('2025-06-30', 'ONLINE', 999, 'D'),
        ('2023-12-31', 'ONLINE', 999, 'A'),
        ('2025-07-01', 'ONLINE', 999, 'A')""")
    run(db)
    assert db.execute('SELECT channel, txn_count, net_amount FROM gold.mart_channel_daily '
                      'ORDER BY txn_date, channel').fetchall() == [
        ('CARD', 1, Decimal('1.00')), ('Card', 2, Decimal('-7.15')),
        ('ONLINE', 2, Decimal('0.00'))]
    row = db.execute("SELECT * FROM gold.mart_channel_daily WHERE channel = 'Card'").fetchone()
    assert row[4] == 20250630
    assert row[5].strftime('%Y-%m-%dT%H:%M:%S.000Z') == '2025-06-30T23:00:00.000Z'
    assert row[6:8] == ('COREBANK', 'A')
    assert [str(v) for v in row[8:]] == ['2024-01-01', '9999-12-31']
    db.execute("INSERT INTO gold.mart_channel_daily SELECT DATE '2023-12-31', channel, "
               "txn_count, net_amount, batch_id, load_ts, src_system_cd, record_status_cd, "
               "effective_date, expiry_date FROM gold.mart_channel_daily WHERE channel = 'Card'")
    before = db.execute('SELECT * FROM gold.mart_channel_daily ORDER BY txn_date, channel').fetchall()
    run(db)
    assert db.execute('SELECT * FROM gold.mart_channel_daily ORDER BY txn_date, channel').fetchall() == before
    db.execute('DELETE FROM silver.fct_transaction')
    run(db)
    assert db.execute('SELECT txn_date FROM gold.mart_channel_daily').fetchall() == [(row[0].replace(year=2023, month=12, day=31),)]


def test_empty_delivery(db):
    run(db)
    assert db.execute('SELECT COUNT(*) FROM gold.mart_channel_daily').fetchone() == (0,)


def test_missing_calendar_rejected_before_write(db):
    run(db)
    db.execute("INSERT INTO silver.fct_transaction VALUES ('2025-01-01', 'CARD', 1, 'A')")
    with pytest.raises(duckdb.Error, match='Channel calendar date is missing'):
        run(db)
    assert db.execute('SELECT COUNT(*) FROM gold.mart_channel_daily').fetchone() == (0,)


def test_duplicate_calendar_reconciles(db):
    db.execute("INSERT INTO bronze.dim_calendar VALUES ('2024-01-01')")
    db.execute("INSERT INTO silver.fct_transaction VALUES ('2024-01-01', 'CARD', 1, 'A')")
    with pytest.raises(duckdb.Error, match='Channel count or signed amount reconciliation failed'):
        run(db)
