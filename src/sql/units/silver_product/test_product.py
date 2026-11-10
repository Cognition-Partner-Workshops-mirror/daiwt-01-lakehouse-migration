import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest


UNIT = Path(__file__).resolve().parent
ROOT = UNIT.parents[3]
SQL = (UNIT / 'product.pipeline.sql').read_text()
COLUMNS = {
    'product_id': 'INT', 'product_name': 'STRING', 'annual_rate': 'DECIMAL(6,2)',
    'batch_id': 'BIGINT', 'load_ts': 'TIMESTAMP_NTZ', 'src_system_cd': 'STRING',
    'record_status_cd': 'STRING', 'effective_date': 'DATE', 'expiry_date': 'DATE',
    'product_family_cd': 'STRING', 'currency_cd': 'STRING', 'product_status_cd': 'STRING',
    'rate_basis_cd': 'STRING', 'minimum_balance': 'DECIMAL(18,2)',
}


def feed():
    with (UNIT / 'product_D0.csv').open() as handle:
        return list(csv.DictReader(handle, delimiter='|'))


def typed(row):
    result = {}
    for column, kind in COLUMNS.items():
        value = row[column]
        if value is None:
            result[column] = None
        elif kind.startswith('DECIMAL'):
            precision, scale = map(int, re.findall(r'\d+', kind))
            value = Decimal(str(value)).quantize(Decimal(10) ** -scale, rounding=ROUND_HALF_UP)
            if abs(value) >= Decimal(10) ** (precision - scale):
                raise ValueError('decimal overflow')
            result[column] = value
        else:
            try:
                if kind in ('INT', 'BIGINT'):
                    value = int(value)
                    bits = 32 if kind == 'INT' else 64
                    if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                        raise ValueError('integer overflow')
                elif kind == 'DATE':
                    value = date.fromisoformat(value).isoformat()
                elif kind == 'TIMESTAMP_NTZ':
                    value = datetime.fromisoformat(value).isoformat(sep=' ')
                result[column] = value
            except ValueError:
                result[column] = None
    return result


def partition(rows):
    expectations = re.findall(r'CONSTRAINT \w+ EXPECT \((.*)\) ON VIOLATION DROP ROW', SQL)
    quarantine = SQL.split('AS SELECT * FROM product_candidates\nWHERE ')[1].rstrip(';\n')
    accepted, rejected = [], []
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE product_candidates (' + ', '.join(
            f'{name} ' + ('NUMERIC' if kind.startswith('DECIMAL') else '')
            for name, kind in COLUMNS.items()) + ')')
        for row in rows:
            db.execute('DELETE FROM product_candidates')
            db.execute('INSERT INTO product_candidates VALUES (' + ','.join('?' * len(COLUMNS)) + ')',
                       [str(value) if isinstance(value, Decimal) else value for value in row.values()])
            valid = db.execute('SELECT ' + ' AND '.join(f'({e})' for e in expectations)
                               + ' FROM product_candidates').fetchone()[0]
            invalid = db.execute('SELECT (' + quarantine + ') FROM product_candidates').fetchone()[0]
            assert bool(valid) != bool(invalid)
            (accepted if valid else rejected).append(row)
    return accepted, rejected


def test_registry_and_sql_contract():
    assert json.loads((UNIT / 'unit.json').read_text()) == {
        'job': 'silver_pipeline', 'legacy_unit': 'foundation', 'outputs': [],
        'owns': ['silver.dim_product', 'silver.dim_product_quarantine'],
        'depends_on': ['silver_pipeline'],
    }
    assert 'FROM ${catalog}.${silver_schema}.asof_edw_dim_product(:drop)' in SQL
    assert '${drop}' not in SQL and 'TEMPORARY VIEW' not in SQL
    assert SQL.count('CREATE OR REFRESH PRIVATE MATERIALIZED VIEW') == 1
    assert SQL.count("TBLPROPERTIES ('delta.feature.timestampNtz'='supported')") == 3
    projection = SQL.split('AS SELECT\n')[1].split('\nFROM ')[0]
    assert re.findall(r' AS ([a-z_]+),?$', projection, re.M) == list(COLUMNS)
    for name, kind in COLUMNS.items():
        expression = (f'CAST(CAST({name} AS STRING) AS {kind})' if kind.startswith('DECIMAL')
                      else f'{"CAST" if kind == "STRING" else "TRY_CAST"}({name} AS {kind})')
        assert f'{expression} AS {name}' in projection
    output = SQL.split('CREATE OR REFRESH MATERIALIZED VIEW dim_product (')[1]
    output = output.split('AS SELECT\n')[1].split('\nFROM ')[0]
    assert [c.strip() for c in output.split(',')] == list(COLUMNS)
    assert len(re.findall(r'CONSTRAINT .* ON VIOLATION DROP ROW', SQL)) == 5
    assert "WHERE '${silver_bad_rows}' = 'true'" in SQL
    assert set(feed()[0]) == set(COLUMNS)
    accessor = (ROOT / 'src/sql/units/silver_asof/functions.sql').read_text()
    signature = accessor.split('`asof_edw_dim_product`(as_of_drop STRING)')[1]
    signature = signature.split('RETURNS TABLE (')[1].split(')\nRETURN SELECT')[0]
    assert signature == ', '.join(f'{name} {kind}' for name, kind in COLUMNS.items())


def test_fixtures_match_captured_manifest():
    manifest = json.loads((ROOT / 'golden/manifest.json').read_text())
    for local, remote in [
        ('product_D0.csv', 'inputs/D0/nvb_edw.dim_product/nvb_edw.dim_product.csv'),
        ('product_D0.expected.json', 'controls/D0/expected/nvb_edw.dim_product.json'),
    ]:
        entry = next(e for e in manifest['files'] if e['path'] == remote)
        assert hashlib.sha256((UNIT / local).read_bytes()).hexdigest() == entry['sha256']


def test_d0_control_metrics():
    expected = json.loads((UNIT / 'product_D0.expected.json').read_text())
    rows, quarantine = partition([typed(row) for row in feed()])
    assert quarantine == []
    assert len(rows) == expected['rows'] == expected['input_rows'][expected['table']]
    assert len({row['product_id'] for row in rows}) == len(rows)
    for column, count in expected['distinct'].items():
        assert len({row[column] for row in rows}) == count
    for column, total in expected['sums'].items():
        assert sum(row[column] for row in rows) == Decimal(str(total))
    for column, limits in expected['dates'].items():
        assert {'min': min(row[column] for row in rows),
                'max': max(row[column] for row in rows)} == limits
    for key, counts in expected.items():
        if key.endswith('_counts'):
            assert dict(Counter(row[key.removesuffix('_counts')] for row in rows)) == counts
    assert all(str(row['minimum_balance']) == '0.00' for row in rows)


@pytest.mark.parametrize('column,value', [
    ('product_id', None), ('product_id', 'bad'), ('product_id', str(2 ** 31)),
    ('batch_id', None), ('batch_id', 'bad'), ('load_ts', None), ('load_ts', 'bad'),
    ('record_status_cd', 'X'), ('minimum_balance', '-0.01'),
])
def test_invalid_rows_quarantined(column, value):
    row = typed(dict(feed()[0], **{column: value}))
    assert partition([row]) == ([], [row])


def test_empty_nullable_and_deleted_rows():
    assert partition([]) == ([], [])
    rows = [typed(dict(feed()[0], record_status_cd='D')),
            typed(dict(feed()[0], record_status_cd=None, minimum_balance=None, annual_rate=None))]
    assert partition(rows) == (rows, [])


@pytest.mark.parametrize('value,expected', [(0, '0.00'), (1.005, '1.01'), (19.9, '19.90')])
def test_printed_decimal_rounding(value, expected):
    assert str(typed(dict(feed()[0], annual_rate=value))['annual_rate']) == expected


@pytest.mark.parametrize('value', ['not-a-number', '10000.00'])
def test_decimal_cast_failure(value):
    with pytest.raises((ValueError, ArithmeticError)):
        typed(dict(feed()[0], annual_rate=value))


def test_demo_switch():
    demo = SQL.split('UNION ALL\nSELECT\n')[1].split('\nWHERE ')[0]
    literals = re.findall(r"CAST\((NULL|'[^']*'|[\d.]+) AS (?:\w+)(?:\(\d+,\d+\))?\)", demo)
    assert len(literals) == len(COLUMNS)
    row = typed(dict(zip(COLUMNS, [None if v == 'NULL' else v.strip("'") for v in literals])))
    normal = [typed(row) for row in feed()]
    assert partition(normal) == (normal, [])
    assert partition(normal + [row]) == (normal, [row])
    assert partition([row]) == ([], [row])
