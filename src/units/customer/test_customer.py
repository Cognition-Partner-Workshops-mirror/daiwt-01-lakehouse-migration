from pathlib import Path

import duckdb
import pytest

from migration_ci.model import statements


@pytest.fixture
def db():
    with duckdb.connect() as connection:
        connection.execute('CREATE SCHEMA bronze; CREATE SCHEMA silver')
        connection.execute('''CREATE TABLE bronze.customer_feed (
            customer_id VARCHAR, customer_name VARCHAR, branch_id VARCHAR,
            town VARCHAR, updated_at VARCHAR)''')
        connection.execute('CREATE TABLE bronze.dim_branch AS SELECT * FROM (VALUES (1), (2)) b(branch_id)')
        yield connection


def run(db):
    sql = Path(__file__).with_name('01_customer.sql').read_text()
    for statement in statements(sql):
        db.execute(statement.sql(dialect='duckdb'))


def test_current_customer_and_defaults(db):
    rows = [
        ('1', ' old ', '1', 'Town', '2025-06-29 12:00:00'),
        ('1', '\u00a0Kari\u00a0Nordli ', '1', '\u00a0Bodo ', '2025-06-30 20:00:00'),
        ('2', 'Name', '1', None, '2025-06-30 20:00:00'),
        ('3', 'Name', '1', '', '2025-06-30 20:00:00'),
    ]
    db.executemany('INSERT INTO bronze.customer_feed VALUES (?, ?, ?, ?, ?)', rows)
    run(db)
    assert db.execute('SELECT customer_name, town FROM silver.dim_customer ORDER BY customer_id').fetchall() == [
        ('Kari Nordli', 'Bodo'), ('Name', None), ('Name', '')
    ]
    assert db.execute('''SELECT batch_id, src_system_cd, record_status_cd,
        CAST(effective_date AS VARCHAR), CAST(expiry_date AS VARCHAR), customer_type_cd,
        residence_country_cd, preferred_language_cd, kyc_status_cd, consent_marketing_ind,
        CAST(load_ts AS VARCHAR) FROM silver.dim_customer WHERE customer_id = 1''').fetchone() == (
        20250630, 'COREBANK', 'A', '2024-01-01', '9999-12-31', 'PERSONAL',
        'NO', 'nb', 'VERIFIED', 'N', '2025-06-30 23:00:00'
    )


@pytest.mark.parametrize('contenders, expected', [
    ([('Alpha', 'Z', '2'), ('Beta', 'A', '1')], ('Beta', 'A', 1)),
    ([('Name', 'Alpha', '2'), ('Name', 'Beta', '1')], ('Name', 'Beta', 1)),
    ([('Name', 'Town', '1'), ('Name', 'Town', '2')], ('Name', 'Town', 2)),
    ([('alpha', 'Town', '1'), ('Beta', 'Town', '1')], ('Beta', 'Town', 1)),
])
def test_tie_breakers(db, contenders, expected):
    db.executemany('INSERT INTO bronze.customer_feed VALUES (?, ?, ?, ?, ?)', [
        ('1', name, branch, town, '2025-06-30 20:00:00') for name, town, branch in contenders
    ])
    run(db)
    assert db.execute('SELECT customer_name, town, branch_id FROM silver.dim_customer').fetchone() == expected


def test_empty_delivery(db):
    run(db)
    assert db.execute('SELECT COUNT(*) FROM silver.dim_customer').fetchone() == (0,)


@pytest.mark.parametrize('column, value', [
    (0, None), (0, 'invalid'), (0, '0'), (0, '-1'), (0, '2147483648'),
    (1, None), (1, ' \u00a0 '), (2, 'invalid'), (2, '99'),
    (4, None), (4, 'not a timestamp'),
])
def test_invalid_delivery_does_not_replace_output(db, column, value):
    db.execute('CREATE TABLE silver.dim_customer AS SELECT 42 AS sentinel')
    row = ['1', 'Name', '1', 'Town', '2025-06-30 20:00:00']
    row[column] = value
    db.execute('INSERT INTO bronze.customer_feed VALUES (?, ?, ?, ?, ?)', row)
    with pytest.raises(duckdb.ConversionException, match='Invalid customer delivery'):
        run(db)
    assert db.execute('SELECT * FROM silver.dim_customer').fetchall() == [(42,)]
