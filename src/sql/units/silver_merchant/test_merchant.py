import csv
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
SQL = (UNIT / 'merchant.pipeline.sql').read_text()
SCHEMA = [
    ('merchant_id', 'INT'), ('merchant_name', 'STRING'), ('category', 'STRING'),
    ('batch_id', 'BIGINT'), ('load_ts', 'TIMESTAMP_NTZ'), ('src_system_cd', 'STRING'),
    ('record_status_cd', 'STRING'), ('effective_date', 'DATE'), ('expiry_date', 'DATE'),
    ('country_cd', 'STRING'), ('settlement_currency_cd', 'STRING'),
    ('acquirer_cd', 'STRING'), ('merchant_status_cd', 'STRING'),
]
NAMES = [name for name, _ in SCHEMA]
DEFAULTS = [20250630, '2025-06-30 23:00:00', 'COREBANK', 'A', '2024-01-01',
            '9999-12-31', 'NO', 'NOK', 'NVB', 'ACTIVE']
EXPECTED = {
    'rows': 80, 'distinct': {'batch_id': 1, 'merchant_id': 80}, 'sums': {},
    'dates': {
        'effective_date': {'min': '2024-01-01', 'max': '2024-01-01'},
        'expiry_date': {'min': '9999-12-31', 'max': '9999-12-31'},
        'load_ts': {'min': '2025-06-30 23:00:00', 'max': '2025-06-30 23:00:00'},
    },
    'category_counts': {'FUEL': 20, 'GROCERY': 20, 'RETAIL': 20, 'TRAVEL': 20},
    'acquirer_cd_counts': {'NVB': 80}, 'country_cd_counts': {'NO': 80},
    'merchant_status_cd_counts': {'ACTIVE': 80}, 'record_status_cd_counts': {'A': 80},
    'settlement_currency_cd_counts': {'NOK': 80}, 'src_system_cd_counts': {'COREBANK': 80},
    'table': 'nvb_edw.dim_merchant',
    'input_rows': {
        'nvb_edw.dim_account': 8000, 'nvb_edw.dim_branch': 8,
        'nvb_edw.dim_calendar': 547, 'nvb_edw.dim_card': 6000,
        'nvb_edw.dim_merchant': 80, 'nvb_edw.dim_product': 4,
        'nvb_edw.fct_daily_balance': 4376000, 'nvb_edw.fct_loan': 1800,
        'nvb_edw.fct_product_order': 3500, 'nvb_stg.customer_feed': 5100,
        'nvb_stg.transaction_feed': 180000,
    },
}


def d0_feed():
    categories = ['GROCERY', 'TRAVEL', 'FUEL', 'RETAIL']
    return [[i, f'Nordhandel {i:03d}', categories[(i - 1) % 4], *DEFAULTS]
            for i in range(1, 81)]


def strict_cast(kind, value):
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
    return str(value)


def run_transform(rows, bad_rows=False):
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE source (' + ', '.join(NAMES) + ')')
        connection.executemany('INSERT INTO source VALUES (' + ','.join('?' for _ in NAMES) + ')', rows)
        for kind in {kind for _, kind in SCHEMA}:
            connection.create_function('cast_' + kind, 1, lambda value, kind=kind: strict_cast(kind, value))
        candidates = SQL.split('AS SELECT', 1)[1].split(';', 1)[0]
        candidates = 'SELECT' + candidates
        candidates = candidates.replace('${catalog}.${silver_schema}.asof_edw_dim_merchant(:drop)', 'source')
        candidates = candidates.replace('${silver_bad_rows}', str(bad_rows).lower())
        candidates = re.sub(r'CAST\(([^()]+) AS (INT|BIGINT|STRING|DATE|TIMESTAMP_NTZ)\)',
                            lambda m: f'cast_{m[2]}({m[1]})', candidates)
        connection.execute('CREATE TEMP TABLE merchant_candidates AS ' + candidates)
        output = SQL.split('CREATE OR REFRESH MATERIALIZED VIEW ${catalog}.${silver_schema}.dim_merchant (')[1]
        expectations = re.findall(r'EXPECT \((.*?)\) ON VIOLATION DROP ROW', output, re.S)
        select = 'SELECT' + output.split('AS SELECT', 1)[1].split(';', 1)[0]
        good = connection.execute(select + ' WHERE ' + ' AND '.join(f'({e})' for e in expectations)).fetchall()
        quarantine = SQL.split('.dim_merchant_quarantine', 1)[1].split('AS SELECT', 1)[1].rstrip().rstrip(';')
        bad = connection.execute('SELECT' + quarantine).fetchall()
        return [dict(row) for row in good], [dict(row) for row in bad]


def test_d0_feed_and_controls_match_capture_hashes():
    manifest = {entry['path']: entry for entry in json.loads((ROOT / 'golden/manifest.json').read_text())['files']}
    feed = io.StringIO(newline='')
    writer = csv.writer(feed, delimiter='|', lineterminator='\n')
    writer.writerow(NAMES)
    writer.writerows(d0_feed())
    assert hashlib.sha256(feed.getvalue().encode()).hexdigest() == manifest[
        'inputs/D0/nvb_edw.dim_merchant/nvb_edw.dim_merchant.csv']['sha256']
    controls = json.dumps(EXPECTED, indent=2, sort_keys=True) + '\n'
    assert hashlib.sha256(controls.encode()).hexdigest() == manifest[
        'controls/D0/expected/nvb_edw.dim_merchant.json']['sha256']


def test_d0_transform_control_metrics_and_exact_rows():
    good, bad = run_transform(d0_feed())
    assert bad == []
    assert good == [dict(zip(NAMES, row)) for row in d0_feed()]
    metrics = {'rows': len(good), 'distinct': {}, 'sums': {}, 'dates': {}}
    for name, _ in SCHEMA:
        values = [row[name] for row in good if row[name] is not None]
        if name.endswith('_id'):
            metrics['distinct'][name] = len(set(values))
        elif name.endswith('_date') or name == 'load_ts':
            metrics['dates'][name] = {'min': min(values), 'max': max(values)}
        if name == 'category' or name.endswith('_cd'):
            metrics[name + '_counts'] = dict(Counter(values))
    assert metrics == {key: value for key, value in EXPECTED.items() if key not in ('table', 'input_rows')}


def test_ddl_order_types_and_materialized_views():
    projection = SQL.split('AS SELECT', 1)[1].split('FROM', 1)[0]
    assert re.findall(r'CAST\((\w+) AS (\w+)\) AS \1', projection) == SCHEMA
    assert SQL.count('CREATE OR REFRESH PRIVATE MATERIALIZED VIEW') == 1
    assert SQL.count('CREATE OR REFRESH MATERIALIZED VIEW') == 2
    assert SQL.count("TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')") == 3
    assert 'TEMPORARY VIEW' not in SQL and '${drop}' not in SQL
    assert 'asof_edw_dim_merchant(:drop)' in SQL
    for select in SQL.split('AS SELECT')[2:]:
        assert [column.strip() for column in select.split('FROM', 1)[0].split(',')[:len(NAMES)]] == NAMES
    bronze = (ROOT / 'src/sql/units/bronze_landing/load.sql').read_text()
    ddl = bronze.split("'.edw_dim_merchant') (")[1].split(') USING DELTA', 1)[0]
    assert re.findall(r'^  (\w+) (INT|BIGINT|STRING|TIMESTAMP_NTZ|DATE)\b', ddl, re.M)[:len(NAMES)] == SCHEMA


@pytest.mark.parametrize('bad_rows', [False, True])
def test_demo_toggle_and_empty_feed(bad_rows):
    for feed in [[], d0_feed()]:
        good, bad = run_transform(feed, bad_rows)
        assert len(good) == len(feed)
        assert len(bad) == int(bad_rows)
        if bad:
            assert bad[0]['merchant_id'] is None
            assert bad[0]['reason_rejected']


@pytest.mark.parametrize('column,value', [
    ('merchant_id', None), ('batch_id', None), ('load_ts', None),
    ('record_status_cd', None), ('record_status_cd', ''), ('record_status_cd', 'X'),
])
def test_invalid_candidates_are_only_quarantined(column, value):
    row = d0_feed()[0]
    row[NAMES.index(column)] = value
    good, bad = run_transform([row])
    assert good == [] and len(bad) == 1
    assert bad[0][column] == value


def test_nullable_strings_deleted_rows_and_no_defaulting():
    row = d0_feed()[0]
    for name, value in [('category', None), ('record_status_cd', 'D'),
                        ('merchant_name', '  Ærlig handel  '), ('src_system_cd', ''),
                        ('effective_date', None), ('merchant_status_cd', None)]:
        row[NAMES.index(name)] = value
    good, bad = run_transform([row, row])
    assert bad == []
    assert good == [dict(zip(NAMES, row))] * 2


@pytest.mark.parametrize('column,value', [
    ('merchant_id', 'not-an-id'), ('merchant_id', 2 ** 31),
    ('batch_id', str(2 ** 63)), ('load_ts', 'invalid'), ('effective_date', '2025-02-30'),
])
def test_strict_typed_input_errors(column, value):
    row = d0_feed()[0]
    row[NAMES.index(column)] = value
    with pytest.raises(sqlite3.OperationalError, match='user-defined function raised exception'):
        run_transform([row])


def test_registry_ownership():
    assert json.loads((UNIT / 'unit.json').read_text()) == {
        'job': 'silver_pipeline', 'legacy_unit': 'foundation', 'outputs': [],
        'owns': ['silver.dim_merchant', 'silver.dim_merchant_quarantine'],
        'depends_on': ['silver_pipeline'],
    }
