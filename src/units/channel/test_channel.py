from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from migration_ci.model import statements


@pytest.fixture
def db():
    with duckdb.connect() as connection:
        connection.execute('CREATE SCHEMA bronze; CREATE SCHEMA silver; CREATE SCHEMA gold')
        connection.execute('''CREATE TABLE silver.fct_transaction (
            txn_date DATE, channel VARCHAR, amount DECIMAL(18, 3), record_status_cd VARCHAR)''')
        connection.execute('CREATE TABLE bronze.dim_calendar (calendar_date DATE)')
        connection.execute("INSERT INTO bronze.dim_calendar VALUES ('2024-01-01'), ('2025-06-30')")
        yield connection


def run(db):
    for name in ('01_validate.sql', '02_mart_channel_daily.sql'):
        for statement in statements(Path(__file__).with_name(name).read_text()):
            db.execute(statement.sql(dialect='duckdb'))


def test_aggregation_and_defaults(db):
    db.execute("""INSERT INTO silver.fct_transaction VALUES
        ('2024-01-01', ' Card ', 1.005, 'A'),
        ('2024-01-01', 'Card', -2.010, 'A'),
        ('2025-06-30', '', NULL, 'A'),
        ('2025-06-30', NULL, NULL, 'A'),
        ('2025-06-30', 'CARD', 9, 'A'),
        ('2025-06-30', 'CARD', 100, 'D'),
        ('2023-12-31', 'CARD', 100, 'A'),
        ('2025-07-01', 'CARD', 100, 'A')""")
    run(db)
    assert db.execute('''SELECT CAST(txn_date AS VARCHAR), channel, txn_count, net_amount
        FROM gold.mart_channel_daily ORDER BY txn_date, channel''').fetchall() == [
        ('2024-01-01', 'Card', 2, Decimal('-1.01')),
        ('2025-06-30', 'CARD', 1, Decimal('9.00')),
        ('2025-06-30', 'ONLINE', 2, Decimal('0.00')),
    ]
    assert db.execute('''SELECT DISTINCT batch_id, strftime(load_ts, '%Y-%m-%d %H:%M:%S'), src_system_cd,
        record_status_cd, CAST(effective_date AS VARCHAR), CAST(expiry_date AS VARCHAR)
        FROM gold.mart_channel_daily''').fetchall() == [
        (20250630, '2025-06-30 23:00:00', 'COREBANK', 'A', '2024-01-01', '9999-12-31')
    ]


def test_empty_delivery(db):
    run(db)
    assert db.execute('SELECT COUNT(*) FROM gold.mart_channel_daily').fetchone() == (0,)


def test_missing_calendar_preserves_output(db):
    db.execute('CREATE TABLE gold.mart_channel_daily AS SELECT 42 AS sentinel')
    db.execute("INSERT INTO silver.fct_transaction VALUES ('2025-01-01', 'CARD', 1, 'A')")
    with pytest.raises(duckdb.ConversionException, match='Missing calendar date'):
        run(db)
    assert db.execute('SELECT * FROM gold.mart_channel_daily').fetchall() == [(42,)]
