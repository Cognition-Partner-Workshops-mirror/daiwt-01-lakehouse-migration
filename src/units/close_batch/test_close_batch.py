from pathlib import Path

import duckdb
import pytest

from migration_ci.model import statements


def execute_unit(db):
    sql = Path(__file__).with_name('close_batch.sql').read_text()
    for statement in statements(sql):
        db.execute(statement.sql(dialect='duckdb'))


@pytest.fixture
def db():
    with duckdb.connect() as connection:
        connection.execute('CREATE SCHEMA silver')
        connection.execute('CREATE SCHEMA gold')
        connection.execute('CREATE TABLE silver.fct_transaction (amount DECIMAL(18,2))')
        connection.execute('INSERT INTO silver.fct_transaction VALUES (10.25), (-2.50)')
        connection.execute('CREATE TABLE gold.mart_channel_daily (txn_count BIGINT, net_amount DECIMAL(18,2))')
        connection.execute('INSERT INTO gold.mart_channel_daily VALUES (2, 7.75)')
        yield connection


def test_materializes_from_empty_output_and_restarts(db):
    execute_unit(db)
    execute_unit(db)
    assert db.execute('''
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'silver' AND table_name = 'etl_batch_control'
          AND column_name = 'row_count'
    ''').fetchone() == ('BIGINT',)
    assert db.execute('''
        SELECT CAST(batch_date AS VARCHAR), job_name, status, row_count, batch_id,
               CAST(load_ts AS VARCHAR), src_system_cd, record_status_cd,
               CAST(effective_date AS VARCHAR), CAST(expiry_date AS VARCHAR),
               last_step_cd, CAST(window_start_date AS VARCHAR),
               CAST(window_end_date AS VARCHAR), reject_count, scheduler_group_cd
        FROM silver.etl_batch_control
    ''').fetchall() == [(
        '2025-06-30', 'NIGHTLY', 'COMPLETE', 2, 20250630,
        '2025-06-30 23:00:00+00', 'COREBANK', 'A', '2024-01-01', '9999-12-31',
        '10_close_batch', '2024-01-01', '2025-06-30', 0, 'NVB_NIGHTLY',
    )]


@pytest.mark.parametrize('mutation', [
    'UPDATE gold.mart_channel_daily SET txn_count = 3',
    'UPDATE gold.mart_channel_daily SET net_amount = 7.76',
    'UPDATE gold.mart_channel_daily SET txn_count = NULL',
    'UPDATE gold.mart_channel_daily SET net_amount = NULL',
    'DELETE FROM gold.mart_channel_daily',
    'DELETE FROM silver.fct_transaction',
])
def test_rejects_unreconciled_release(db, mutation):
    db.execute(mutation)
    with pytest.raises(duckdb.ConversionException, match='close_batch control gate failed'):
        execute_unit(db)


def test_rejects_both_inputs_empty(db):
    db.execute('DELETE FROM gold.mart_channel_daily')
    db.execute('DELETE FROM silver.fct_transaction')
    with pytest.raises(duckdb.ConversionException, match='close_batch control gate failed'):
        execute_unit(db)
