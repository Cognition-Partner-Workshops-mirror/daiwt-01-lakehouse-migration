import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

UNIT = Path(__file__).parent
SQL = (UNIT / 'customer.pipeline.sql').read_text()


def try_int(value):
    try:
        result = int(value)
        return result if -(2**31) <= result < 2**31 else None
    except (TypeError, ValueError):
        return None


def try_timestamp(value, pattern):
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').isoformat() + '.000Z'
    except (TypeError, ValueError):
        return None


def execute(feed, branches=(1, 2), bad_rows=False):
    """Run the production SELECTs with SQLite adapters for Spark-only syntax."""
    db = sqlite3.connect(':memory:')
    db.create_function('try_int', 1, try_int)
    db.create_function('try_to_timestamp', 2, try_timestamp)
    db.create_function('concat', -1, lambda *parts: ''.join(map(str, parts)))
    db.create_function('raise_error', 1, lambda message: (_ for _ in ()).throw(ValueError(message)))
    db.execute('CREATE TABLE feed (customer_id, customer_name, branch_id, town, updated_at)')
    db.executemany('INSERT INTO feed VALUES (?, ?, ?, ?, ?)', feed)
    db.execute('CREATE TABLE branches (branch_id INTEGER)')
    db.executemany('INSERT INTO branches VALUES (?)', [(b,) for b in branches])
    for statement in SQL.split(';'):
        if not statement.strip():
            continue
        statement = statement.replace('${catalog}.${silver_schema}.dim_customer', 'dim_customer')
        name = re.search(r'MATERIALIZED VIEW (\w+)', statement)[1]
        query = re.split(r'\bAS\s+SELECT\b', statement, maxsplit=1)[1]
        query = 'SELECT' + query
        query = query.replace('${catalog}.${silver_schema}.asof_stg_customer_feed(:drop)', 'feed')
        query = query.replace('${catalog}.${silver_schema}.dim_branch', 'branches')
        query = query.replace('${silver_bad_rows}', str(bad_rows).lower())
        query = re.sub(r'try_cast\(trim\((\w+)\) AS INT\)', r'try_int(trim(\1))', query)
        query = re.sub(r"DATE ('[^']*')", r'\1', query)
        query = re.sub(r'\b(TIMESTAMP|STRING)\b', 'TEXT', query)
        if 'QUALIFY' in query:
            body, window = query.split('QUALIFY')
            columns, source = body.split('FROM', 1)
            window = window.rsplit('= 1', 1)[0].strip()
            query = f'{columns} FROM (SELECT *, {window} AS _rn FROM {source}) WHERE _rn = 1'
        db.execute(f'CREATE VIEW {name} AS {query}')
    return db


def row(key='1', name='Ada', branch='1', town='Oslo', updated='2025-06-30 20:00:00'):
    return key, name, branch, town, updated


def test_normal_defaults_and_column_order():
    db = execute([row(name='\u00a0 Ada\u00a0Nord \u00a0', town='\u00a0 Oslo \u00a0')])
    cursor = db.execute('SELECT * FROM dim_customer')
    result = cursor.fetchone()
    assert result[:6] == (1, 'Ada Nord', 1, 'Oslo', '2025-06-30T20:00:00.000Z', 20250630)
    assert result[6] == '2025-06-30 23:00:00'
    assert result[7:] == ('COREBANK', 'A', '2024-01-01', '9999-12-31', 'PERSONAL', 'NO', 'nb', 'VERIFIED', 'N')
    contract = json.loads((UNIT.parents[3] / 'src/parity/contract.json').read_text())
    output = next(o for o in contract['outputs'] if o['table'] == 'silver.dim_customer')
    assert [c[0] for c in cursor.description if c[0] != 'load_ts'] == [c['name'] for c in output['columns']]
    types = {'INTEGER': 'INT', 'BIGINT': 'BIGINT', 'TIMESTAMP(0)': 'TIMESTAMP', 'DATE': 'DATE'}
    for column in output['columns']:
        expected = types.get(column['source_type'], 'STRING')
        assert re.search(r'\b' + column['name'] + r' ' + expected + r',', SQL)
    assert 'load_ts TIMESTAMP,' in SQL
    assert db.execute('SELECT count(*) FROM dim_customer_quarantine').fetchone() == (0,)


def test_empty_delivery():
    db = execute([])
    assert db.execute('SELECT * FROM dim_customer').fetchall() == []
    assert db.execute('SELECT * FROM dim_customer_quarantine').fetchall() == []


@pytest.mark.parametrize('invalid', [
    row(key=None), row(key='0'), row(key='-1'), row(key='bad'), row(key='2147483648'),
    row(name=None), row(name=' \u00a0 '), row(updated=None), row(updated='bad'),
    row(branch='99'), row(branch=None), row(branch='bad'),
])
def test_rejects_whole_delivery_before_dedup(invalid):
    db = execute([row(), invalid])
    assert db.execute('SELECT count(*) FROM dim_customer_quarantine').fetchone() == (1,)
    with pytest.raises(sqlite3.OperationalError, match='user-defined function raised exception'):
        db.execute('SELECT * FROM dim_customer').fetchall()


def test_demo_switch_does_not_inject_into_fail_gated_unit():
    db = execute([row()], bad_rows=True)
    assert db.execute('SELECT count(*) FROM dim_customer_quarantine').fetchone() == (0,)
    assert db.execute('SELECT * FROM dim_customer').fetchall() == execute([row()]).execute('SELECT * FROM dim_customer').fetchall()


@pytest.mark.parametrize('older,newer', [
    (row(updated='2025-01-01 00:00:00'), row()),
    (row(name='Ada'), row(name='Zoe')),
    (row(town='Bergen'), row(town='Oslo')),
    (row(branch='1'), row(branch='2')),
    (row(town=None), row(town='Oslo')),
])
def test_dedup_all_descending_tiebreakers(older, newer):
    for feed in ([older, newer], [newer, older]):
        db = execute(feed)
        result = db.execute('SELECT customer_name, branch_id, town, updated_at FROM dim_customer').fetchall()
        assert result == [(newer[1], int(newer[2]), newer[3], try_timestamp(newer[4], None))]


def test_spark_contract():
    spec = json.loads((UNIT / 'unit.json').read_text())
    assert spec == dict(job='silver_pipeline', legacy_unit='customer', outputs=['silver.dim_customer'],
                        owns=['silver.dim_customer_quarantine'], depends_on=['silver_pipeline', 'silver_branch'])
    assert 'TEMPORARY' not in SQL and 'TIMESTAMP_NTZ' not in SQL
    assert SQL.count('ON VIOLATION FAIL UPDATE') == 4
    assert 'ON VIOLATION DROP ROW' not in SQL
    assert 'raise_error' in SQL and 'asof_stg_customer_feed(:drop)' in SQL
    assert SQL.index('WHERE NOT valid_customer AND NOT _demo_row') < SQL.index('QUALIFY')
    assert 'MATERIALIZED VIEW ${catalog}.${silver_schema}.dim_customer (' in SQL
    assert 'MATERIALIZED VIEW ${catalog}.${silver_schema}.dim_customer_quarantine AS' in SQL
