import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from bronze_schema import bronze_tables
from registry import load

UNIT = Path(__file__).resolve().parent
ROOT = UNIT.parents[3]
SQL = (UNIT / 'calendar.pipeline.sql').read_text()
COLUMNS = bronze_tables(ROOT)['edw_dim_calendar']
NAMES = [column['name'] for column in COLUMNS]
PREDICATE = SQL.split('EXPECT (', 1)[1].split(') ON VIOLATION', 1)[0]


def source_rows():
    with (UNIT / 'D0.dim_calendar.csv').open() as stream:
        return [[None if row[name] == r'\N' else row[name] for name in NAMES]
                for row in csv.DictReader(stream, delimiter='|')]


def evaluate(rows, bad_rows=False):
    # SQLite exercises the relational SQL; date/timestamp casts remain Spark-only.
    query = SQL.split('AS SELECT', 1)[1].split(';', 1)[0]
    query = 'SELECT' + query
    query = query.replace('${catalog}.${silver_schema}.asof_edw_dim_calendar(:drop)', 'source')
    query = query.replace('${silver_bad_rows}', str(bad_rows).lower())
    query = re.sub(r'AS (DATE|TIMESTAMP_NTZ|STRING)\b', 'AS TEXT', query)
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE source (' + ', '.join(NAMES) + ')')
        db.executemany('INSERT INTO source VALUES (' + ','.join('?' for _ in NAMES) + ')', rows)
        db.execute('CREATE TABLE calendar_candidates AS ' + query)
        output = SQL.split("TBLPROPERTIES ('delta.feature.timestampNtz'='supported')", 2)[2]
        output = output.split('AS SELECT', 1)[1].split(';', 1)[0]
        valid = [dict(row) for row in db.execute('SELECT' + output + ' WHERE ' + PREDICATE)]
        quarantine = SQL.rsplit('AS SELECT', 1)[1].rstrip().rstrip(';')
        invalid = [dict(row) for row in db.execute('SELECT' + quarantine)]
        return valid, invalid


def test_contract_and_types():
    spec = load(ROOT)['silver_calendar']
    assert spec == dict(job='silver_pipeline', legacy_unit='foundation', outputs=[],
                        owns=['silver.dim_calendar', 'silver.dim_calendar_quarantine'],
                        depends_on=['silver_pipeline'])
    casts = re.findall(r'CAST\((\w+) AS (\w+)\) AS (\w+)', SQL)
    assert casts == [(column['name'], column['type'], column['name']) for column in COLUMNS]
    assert SQL.count("TBLPROPERTIES ('delta.feature.timestampNtz'='supported')") == 3
    assert 'CREATE OR REFRESH PRIVATE MATERIALIZED VIEW calendar_candidates' in SQL
    assert 'TEMPORARY' not in SQL and '${drop}' not in SQL
    assert 'asof_edw_dim_calendar(:drop)' in SQL
    manifest = json.loads((ROOT / 'golden/manifest.json').read_text())
    for filename, path in [('D0.dim_calendar.csv', 'inputs/D0/nvb_edw.dim_calendar/nvb_edw.dim_calendar.csv'),
                           ('D0.expected.json', 'controls/D0/expected/nvb_edw.dim_calendar.json')]:
        entry = next(entry for entry in manifest['files'] if entry['path'] == path)
        assert hashlib.sha256((UNIT / filename).read_bytes()).hexdigest() == entry['sha256']


def test_d0_control_metrics_and_pass_through():
    expected = json.loads((UNIT / 'D0.expected.json').read_text())
    result, quarantine = evaluate(source_rows())
    assert quarantine == []
    assert len(result) == expected['rows']
    assert list(result[0]) == NAMES
    for column, bounds in expected['dates'].items():
        assert min(row[column] for row in result) == bounds['min']
        assert max(row[column] for row in result) == bounds['max']
    for column, total in expected['sums'].items():
        assert sum(row[column] for row in result) == total
    for column, distinct in expected['distinct'].items():
        assert len({row[column] for row in result}) == distinct
    for column in ['record_status_cd', 'src_system_cd', 'business_day_rule_cd']:
        assert dict(Counter(row[column] for row in result)) == expected[column + '_counts']
    for actual, source in zip(result, source_rows()):
        assert [str(actual[name]) if actual[name] is not None else None for name in NAMES] == source


def test_empty_and_demo_switch():
    assert evaluate([]) == ([], [])
    good, bad = evaluate(source_rows(), bad_rows=True)
    assert len(good) == 547 and len(bad) == 1
    assert bad[0]['calendar_date'] is None and bad[0]['reason']
    assert evaluate([], bad_rows=True)[0] == []
    assert len(evaluate([], bad_rows=True)[1]) == 1


@pytest.mark.parametrize('column,value', [('calendar_date', None), ('batch_id', None),
                                         ('load_ts', None), ('record_status_cd', 'X')])
def test_invalid_rows_are_quarantined(column, value):
    row = source_rows()[0]
    row[NAMES.index(column)] = value
    good, bad = evaluate([row])
    assert good == [] and len(bad) == 1
    assert bad[0][column] == value


@pytest.mark.parametrize('status', ['D', None])
def test_nullable_and_inactive_status_are_preserved(status):
    row = source_rows()[0]
    row[NAMES.index('record_status_cd')] = status
    good, bad = evaluate([row])
    assert bad == [] and len(good) == 1
    assert good[0]['record_status_cd'] == status
