"""Offline SELECT execution with SQLite; Lakeflow DDL is checked separately.

SQLite substitutes the as-of reader, string/date casts and concat_ws, and
applies the SQL's DROP ROW predicates. This does not compile Lakeflow SQL.
"""
import csv
import json
import re
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest


UNIT = Path(__file__).parent
ROOT = UNIT.parents[3]
SQL = (UNIT / 'branch.pipeline.sql').read_text()
COLUMNS = [
    ('branch_id', 'INT'), ('town', 'STRING'), ('batch_id', 'BIGINT'),
    ('load_ts', 'TIMESTAMP_NTZ'), ('src_system_cd', 'STRING'),
    ('record_status_cd', 'STRING'), ('effective_date', 'DATE'),
    ('expiry_date', 'DATE'), ('branch_name', 'STRING'), ('region_cd', 'STRING'),
    ('branch_type_cd', 'STRING'), ('service_language_cd', 'STRING'),
    ('branch_status_cd', 'STRING'),
]
NAMES = [name for name, _ in COLUMNS]
WIDTHS = dict(town=30, src_system_cd=12, branch_name=80, region_cd=8,
              branch_type_cd=10, service_language_cd=2, branch_status_cd=10)


def fixture_rows():
    with (UNIT / 'd0_branch.csv').open() as handle:
        return list(csv.DictReader(handle, delimiter='|'))


def execute(rows, bad_rows=False):
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        db.create_function('concat_ws', -1,
                           lambda sep, *items: sep.join(x for x in items if x is not None))
        db.execute('CREATE TABLE bronze (' + ', '.join(NAMES) + ')')
        db.executemany('INSERT INTO bronze VALUES (' + ','.join('?' for _ in NAMES) + ')',
                       [[row[name] for name in NAMES] for row in rows])
        for statement in SQL.strip().rstrip(';').split(';\n\n'):
            header, query = statement.split('\nAS SELECT', 1)
            name = re.search(r'MATERIALIZED VIEW (\S+)', header)[1]
            name = name.replace('${catalog}.${silver_schema}.', '')
            query = 'SELECT' + query.rstrip(';')
            query = query.replace('${catalog}.${silver_schema}.asof_edw_dim_branch(:drop)', 'bronze')
            query = query.replace('${silver_bad_rows}', str(bad_rows).lower())
            query = re.sub(r'AS (STRING|DATE|TIMESTAMP_NTZ)\b', 'AS TEXT', query)
            predicates = re.findall(r'EXPECT \((.*?)\) ON VIOLATION DROP ROW', header)
            if predicates:
                query = 'SELECT * FROM (' + query + ') WHERE ' + ' AND '.join(
                    '(' + predicate + ')' for predicate in predicates)
            db.execute('CREATE TABLE ' + name + ' AS ' + query)
        return tuple([dict(row) for row in db.execute('SELECT * FROM ' + table)]
                     for table in ['dim_branch', 'dim_branch_quarantine'])


def test_sql_contract():
    spec = json.loads((UNIT / 'unit.json').read_text())
    assert spec == dict(job='silver_pipeline', legacy_unit='foundation', outputs=[],
                        owns=['silver.dim_branch', 'silver.dim_branch_quarantine'],
                        depends_on=['silver_pipeline'])
    contract = json.loads((ROOT / 'src/parity/contract.json').read_text())
    assert not set(spec['owns']) & {entry['table'] for entry in contract['outputs']}
    assert '${catalog}.${silver_schema}.asof_edw_dim_branch(:drop)' in SQL
    assert '${drop}' not in SQL and 'TEMPORARY VIEW' not in SQL
    statements = SQL.strip().rstrip(';').split(';\n\n')
    assert len(statements) == 5
    for statement in statements:
        assert re.match(r'CREATE OR REFRESH (PRIVATE )?MATERIALIZED VIEW', statement)
        assert "TBLPROPERTIES ('delta.feature.timestampNtz'='supported')\nAS" in statement
    header, body = statements[3].split('\nAS SELECT', 1)
    assert re.findall(r'^  (\w+) (INT|STRING|BIGINT|TIMESTAMP_NTZ|DATE)[,\n]', header, re.M) == COLUMNS
    assert body.strip() == ', '.join(NAMES) + '\nFROM branch_validated'
    assert 'EXPECT (branch_key_count = 1) ON VIOLATION DROP ROW' in SQL
    assert 'EXPECT (reason IS NULL) ON VIOLATION DROP ROW' in SQL
    for name, width in WIDTHS.items():
        assert f'length({name}) > {width}' in SQL
    assert "WHERE '${silver_bad_rows}' = 'true'" in SQL
    assert 'WHERE reason IS NOT NULL' in statements[4]


def test_d0_controls_and_pass_through():
    source = fixture_rows()
    good, quarantine = execute(source)
    expected = json.loads((UNIT / 'd0_controls.json').read_text())
    assert quarantine == []
    assert len(good) == expected['rows'] == expected['input_rows'][expected['table']]
    for actual, original in zip(good, source):
        assert list(actual) == NAMES
        assert actual == {name: int(original[name]) if kind in ('INT', 'BIGINT')
                          else original[name] for name, kind in COLUMNS}
        datetime.fromisoformat(actual['load_ts'])
        date.fromisoformat(actual['effective_date'])
        date.fromisoformat(actual['expiry_date'])
    for name, count in expected['distinct'].items():
        assert len({row[name] for row in good}) == count
    for name, bounds in expected['dates'].items():
        assert dict(min=min(row[name] for row in good), max=max(row[name] for row in good)) == bounds
    for key, counts in expected.items():
        if key.endswith('_counts'):
            assert dict(Counter(row[key.removesuffix('_counts')] for row in good)) == counts
    assert expected['sums'] == {}


def test_demo_flag_and_empty():
    assert execute([]) == ([], [])
    baseline, _ = execute(fixture_rows())
    good, bad = execute(fixture_rows(), bad_rows=True)
    assert good == baseline and len(bad) == 1
    assert 'null branch_id' in bad[0]['reason']
    assert 'invalid record_status_cd' in bad[0]['reason']
    assert execute([], bad_rows=True)[0] == []
    assert len(execute([], bad_rows=True)[1]) == 1


@pytest.mark.parametrize('name,value,reason', [
    ('branch_id', None, 'null branch_id'),
    ('batch_id', None, 'null batch_id'),
    ('load_ts', None, 'null load_ts'),
    ('record_status_cd', None, 'invalid record_status_cd'),
    ('record_status_cd', '', 'invalid record_status_cd'),
    ('record_status_cd', 'X', 'invalid record_status_cd'),
] + [(name, 'x' * (width + 1), f'{name} exceeds {width}') for name, width in WIDTHS.items()])
def test_invalid_rows_are_quarantined(name, value, reason):
    row = {**fixture_rows()[0], name: value}
    good, bad = execute([row])
    assert good == [] and len(bad) == 1
    assert reason in bad[0]['reason']


def test_all_duplicate_keys_quarantined():
    rows = fixture_rows()
    good, bad = execute(rows + [rows[0]])
    assert len(good) == 7 and len(bad) == 2
    assert {row['reason'] for row in bad} == {'duplicate branch_id'}


def test_nullable_columns_boundaries_and_deleted_records():
    row = fixture_rows()[0]
    for name, width in WIDTHS.items():
        row[name] = 'x' * width
    row['record_status_cd'] = 'D'
    good, bad = execute([row])
    assert not bad and good[0]['record_status_cd'] == 'D'
    for name in [*WIDTHS, 'effective_date', 'expiry_date']:
        row[name] = None
    good, bad = execute([row])
    assert len(good) == 1 and not bad
