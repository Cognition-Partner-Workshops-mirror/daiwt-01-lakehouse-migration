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
SQL = (UNIT / 'product_order.pipeline.sql').read_text()
COLUMNS = [
    ('order_id', 'INT'), ('customer_id', 'INT'), ('product_id', 'INT'),
    ('order_date', 'DATE'), ('status', 'STRING'), ('batch_id', 'BIGINT'),
    ('load_ts', 'TIMESTAMP_NTZ'), ('src_system_cd', 'STRING'),
    ('record_status_cd', 'STRING'), ('effective_date', 'DATE'),
    ('expiry_date', 'DATE'), ('application_channel_cd', 'STRING'),
    ('decision_system_cd', 'STRING'), ('currency_cd', 'STRING'),
    ('manual_review_ind', 'STRING'),
]


def typed(row):
    result = {}
    for name, kind in COLUMNS:
        value = row[name]
        if value is not None and kind != 'STRING':
            value = None if value == '' else {
                'INT': int, 'BIGINT': int, 'DATE': date.fromisoformat,
                'TIMESTAMP_NTZ': datetime.fromisoformat,
            }[kind](value)
        result[name] = value
    return result


def fixture_rows():
    data = gzip.decompress((UNIT / 'D0.product_order.csv.gz').read_bytes())
    return [typed(row) for row in csv.DictReader(io.StringIO(data.decode()), delimiter='|')]


def test_fixture_checksums():
    manifest = json.loads((ROOT / 'golden/manifest.json').read_text())
    for filename, path in [
        ('D0.product_order.csv.gz', 'inputs/D0/nvb_edw.fct_product_order/nvb_edw.fct_product_order.csv'),
        ('D0.expected.json', 'controls/D0/expected/nvb_edw.fct_product_order.json'),
    ]:
        data = (UNIT / filename).read_bytes()
        if filename.endswith('.gz'):
            data = gzip.decompress(data)
        entry = next(e for e in manifest['files'] if e['path'] == path)
        assert hashlib.sha256(data).hexdigest() == entry['sha256']


def test_d0_all_control_metrics():
    rows = fixture_rows()
    expected = json.loads((UNIT / 'D0.expected.json').read_text())
    actual = {'rows': len(rows), 'distinct': {}, 'sums': {}, 'dates': {}}
    for name, kind in COLUMNS:
        values = [row[name] for row in rows if row[name] is not None]
        if name.endswith('_id'):
            actual['distinct'][name] = len(set(values))
        elif kind in ('INT', 'BIGINT'):
            actual['sums'][name] = sum(values)
        elif kind in ('DATE', 'TIMESTAMP_NTZ'):
            actual['dates'][name] = {'min': str(min(values)), 'max': str(max(values))}
        if name == 'status' or name.endswith(('_cd', '_ind')):
            actual[name + '_counts'] = dict(Counter(values))
    assert actual == {k: v for k, v in expected.items() if k not in ('table', 'input_rows')}


def test_sql_structure_and_registry():
    assert re.findall(r'CAST\((\w+) AS (\w+)\) AS \1', SQL) == COLUMNS
    names = ', '.join(name for name, _ in COLUMNS)
    for output in ('fct_product_order', 'fct_product_order_quarantine'):
        section = SQL.split('CREATE OR REFRESH MATERIALIZED VIEW ' + output, 1)[1]
        projection = section.split('AS SELECT ', 1)[1].split('FROM ', 1)[0]
        assert ' '.join(projection.split()) == names
    assert 'CREATE OR REFRESH PRIVATE MATERIALIZED VIEW product_order_candidates' in SQL
    assert 'TEMPORARY' not in SQL and '${drop}' not in SQL
    assert '${catalog}.${silver_schema}.asof_edw_fct_product_order(:drop)' in SQL
    assert SQL.count("TBLPROPERTIES ('delta.feature.timestampNtz'='supported')") == 3
    assert "WHERE '${silver_bad_rows}' = 'true'" in SQL
    assert SQL.count('ON VIOLATION DROP ROW') == 4
    assert 'DISTINCT' not in SQL and 'coalesce' not in SQL.split('UNION ALL')[0]
    spec = json.loads((UNIT / 'unit.json').read_text())
    assert spec == dict(job='silver_pipeline', legacy_unit='foundation', outputs=[],
                        owns=['silver.fct_product_order', 'silver.fct_product_order_quarantine'],
                        depends_on=['silver_pipeline'])


def quarantine(rows):
    predicate = SQL.rsplit('WHERE ', 1)[1].rstrip().removesuffix(';')
    names = [name for name, _ in COLUMNS]
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE product_order_candidates (' + ', '.join(names) + ')')
        db.executemany('INSERT INTO product_order_candidates VALUES (' + ','.join('?' for _ in names) + ')',
                       [tuple(str(row[n]) if isinstance(row[n], (date, datetime)) else row[n]
                              for n in names) for row in rows])
        return db.execute('SELECT * FROM product_order_candidates WHERE ' + predicate).fetchall()


def test_normal_and_empty_quarantine():
    assert quarantine(fixture_rows()) == []
    assert quarantine([]) == []


@pytest.mark.parametrize('field,value', [
    ('order_id', None), ('record_status_cd', None), ('record_status_cd', ''),
    ('record_status_cd', 'X'), ('batch_id', None), ('load_ts', None),
])
def test_failed_expectations_are_quarantined(field, value):
    row = {**fixture_rows()[0], field: value}
    assert len(quarantine([row])) == 1
    expressions = re.findall(r'CONSTRAINT \w+ EXPECT \((.*?)\) ON VIOLATION DROP ROW(?:,|\n)', SQL)
    assert len(expressions) == 4
    assert f'{field} ' in ' '.join(expressions)


def test_inactive_and_null_optional_columns_are_preserved():
    row = {**fixture_rows()[0], 'record_status_cd': 'D', 'effective_date': None,
           'status': None, 'manual_review_ind': None}
    raw = {name: str(value) if value is not None else None for name, value in row.items()}
    assert typed(raw) == row
    assert quarantine([row]) == []


@pytest.mark.parametrize('field,value', [('order_id', 'bad'), ('order_date', 'bad'), ('load_ts', 'bad')])
def test_malformed_typed_inputs_fail(field, value):
    raw = {name: str(item) for name, item in fixture_rows()[0].items()}
    with pytest.raises(ValueError):
        typed({**raw, field: value})
