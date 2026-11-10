import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

UNIT = Path(__file__).resolve().parent
ROOT = UNIT.parents[3]
SQL = (UNIT / 'card.pipeline.sql').read_text()
MANIFEST = json.loads((ROOT / 'golden/manifest.json').read_text())
PROJECTION = SQL.split('AS SELECT\n', 1)[1].split('\nFROM ', 1)[0]
COLUMNS = re.findall(r' AS (\w+)(?:,|$)', PROJECTION)
TYPES = re.findall(r' AS (\w+)\) AS ', PROJECTION)


def cast(value, kind):
    if value is None:
        return None
    if kind in ('INT', 'BIGINT'):
        result = int(value)
        bits = 32 if kind == 'INT' else 64
        if not -(2 ** (bits - 1)) <= result < 2 ** (bits - 1):
            raise ValueError('integer overflow')
        return result
    if kind == 'DATE':
        return date.fromisoformat(value).isoformat()
    if kind == 'TIMESTAMP_NTZ':
        return datetime.fromisoformat(value).isoformat(sep=' ')
    assert kind == 'STRING'
    return str(value)


def local_sql(query):
    return re.sub(r"CAST\(((?:NULLIF\(\w+, ''\)|[^()])+?) AS (INT|BIGINT|STRING|DATE|TIMESTAMP_NTZ)\)",
                  lambda m: f"typed_cast({m[1]}, '{m[2]}')", query)


def build(rows, bad_rows=False):
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        db.create_function('typed_cast', 2, cast)
        db.execute('CREATE TABLE feed (' + ', '.join(COLUMNS) + ')')
        db.executemany('INSERT INTO feed VALUES (' + ','.join('?' for _ in COLUMNS) + ')',
                       [[row[c] for c in COLUMNS] for row in rows])
        db.execute('CREATE TABLE card_typed AS SELECT ' + local_sql(PROJECTION) + ' FROM feed')
        candidates = SQL.split('AS SELECT * FROM card_typed', 1)[1].split(';', 1)[0]
        candidates = candidates.replace('${silver_bad_rows}', str(bad_rows).lower())
        db.execute('CREATE TABLE card_candidates AS SELECT * FROM card_typed' + local_sql(candidates))
        expectations = re.findall(r'EXPECT \((.*?)\) ON VIOLATION DROP ROW', SQL)
        valid = ' AND '.join('(' + expression + ')' for expression in expectations)
        output = SQL.split("AS SELECT card_id, account_id", 1)[1].split(';', 1)[0]
        result = db.execute('SELECT card_id, account_id' + output + ' WHERE ' + valid).fetchall()
        quarantine = SQL.rsplit('AS SELECT ', 1)[1].rstrip().removesuffix(';')
        rejected = db.execute('SELECT ' + quarantine).fetchall()
        return [dict(row) for row in result], [dict(row) for row in rejected]


@pytest.fixture
def rows():
    content = gzip.decompress((UNIT / 'd0_dim_card.csv.gz').read_bytes())
    entry = next(e for e in MANIFEST['files'] if e['path'].endswith('nvb_edw.dim_card.csv'))
    assert hashlib.sha256(content).hexdigest() == entry['sha256']
    return list(csv.DictReader(io.StringIO(content.decode()), delimiter='|'))


def test_d0_control_metrics(rows):
    control_bytes = (UNIT / 'd0_dim_card.expected.json').read_bytes()
    entry = next(e for e in MANIFEST['files'] if e['path'] == 'controls/D0/expected/nvb_edw.dim_card.json')
    assert hashlib.sha256(control_bytes).hexdigest() == entry['sha256']
    expected = json.loads(control_bytes)
    result, rejected = build(rows)
    assert not rejected
    assert len(result) == expected['rows'] == expected['input_rows'][expected['table']]
    assert list(result[0]) == COLUMNS
    assert len({r['card_id'] for r in result}) == len(result)
    for column, value in expected['distinct'].items():
        assert len({r[column] for r in result}) == value
    for column, bounds in expected['dates'].items():
        assert {'min': min(r[column] for r in result), 'max': max(r[column] for r in result)} == bounds
    for metric, value in expected.items():
        if metric.endswith('_counts'):
            assert dict(Counter(r[metric.removesuffix('_counts')] for r in result)) == value
    for column, value in expected['sums'].items():
        assert sum(r[column] for r in result) == value
    assert result == [{c: cast(row[c], kind) for c, kind in zip(COLUMNS, TYPES)} for row in rows]


def test_empty_and_bad_row_switch(rows):
    assert build([]) == ([], [])
    result, rejected = build([], bad_rows=True)
    assert result == [] and len(rejected) == 1 and rejected[0]['card_id'] is None
    normal, _ = build(rows)
    result, rejected = build(rows, bad_rows=True)
    assert result == normal and len(rejected) == 1


@pytest.mark.parametrize('column,value', [('card_id', None), ('batch_id', None),
                                         ('load_ts', None), ('record_status_cd', 'X')])
def test_quarantine_matches_expectations(rows, column, value):
    result, rejected = build([{**rows[0], column: value}])
    assert result == [] and len(rejected) == 1


@pytest.mark.parametrize('status', ['A', 'D', None])
def test_status_preserved(rows, status):
    result, rejected = build([{**rows[0], 'record_status_cd': status}])
    assert not rejected and result[0]['record_status_cd'] == status


@pytest.mark.parametrize('column', [c for c, kind in zip(COLUMNS, TYPES)
                                   if kind == 'STRING' and c != 'record_status_cd'])
def test_bronze_empty_string_sentinel_restores_null(rows, column):
    result, rejected = build([{**rows[0], 'record_status_cd': '', column: ''}])
    assert not rejected and len(result) == 1
    assert result[0]['record_status_cd'] is None
    assert result[0][column] is None


@pytest.mark.parametrize('column,value', [('card_id', 'not-an-integer'), ('card_id', '2147483648'),
                                         ('effective_date', 'not-a-date'), ('load_ts', 'not-a-timestamp')])
def test_invalid_cast_fails(rows, column, value):
    with pytest.raises(sqlite3.OperationalError, match='user-defined function raised exception'):
        build([{**rows[0], column: value}])


def test_pipeline_contract():
    entry = next(e for e in MANIFEST['files'] if e['path'].endswith('nvb_edw.dim_card.csv'))
    assert COLUMNS == entry['columns']
    assert TYPES == ['INT', 'INT', 'STRING', 'BIGINT', 'TIMESTAMP_NTZ', 'STRING', 'STRING',
                     'DATE', 'DATE', 'STRING', 'STRING', 'DATE', 'STRING', 'STRING']
    assert 'FROM ${catalog}.${silver_schema}.asof_edw_dim_card(:drop)' in SQL
    assert 'TEMPORARY VIEW' not in SQL
    assert SQL.count("TBLPROPERTIES ('delta.feature.timestampNtz'='supported')") == 4
    assert SQL.count('CREATE OR REFRESH PRIVATE MATERIALIZED VIEW') == 2
    assert json.loads((UNIT / 'unit.json').read_text()) == {
        'job': 'silver_pipeline', 'legacy_unit': 'foundation', 'outputs': [],
        'owns': ['silver.dim_card', 'silver.dim_card_quarantine'], 'depends_on': ['silver_pipeline']}
