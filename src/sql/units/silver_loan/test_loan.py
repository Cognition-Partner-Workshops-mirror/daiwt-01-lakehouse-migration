import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest


UNIT = Path(__file__).resolve().parent
ROOT = UNIT.parents[3]
SQL = (UNIT / 'loan.pipeline.sql').read_text()
SCHEMA = [
    ('loan_id', 'INT'), ('account_id', 'INT'), ('outstanding', 'DECIMAL(18,2)'),
    ('arrears_days', 'INT'), ('batch_id', 'BIGINT'), ('load_ts', 'TIMESTAMP_NTZ'),
    ('src_system_cd', 'STRING'), ('record_status_cd', 'STRING'),
    ('effective_date', 'DATE'), ('expiry_date', 'DATE'), ('loan_status_cd', 'STRING'),
    ('currency_cd', 'STRING'), ('contractual_rate', 'DECIMAL(6,2)'),
    ('repayment_frequency_cd', 'STRING'), ('collateral_type_cd', 'STRING'),
]
COLUMNS = [name for name, _ in SCHEMA]
PREDICATE = SQL.split('CONSTRAINT valid_loan EXPECT (')[1].split(') ON VIOLATION')[0]
PROJECTION = SQL.split('AS SELECT ')[2].split('FROM loan_candidates;')[0]
QUARANTINE = SQL.split('CREATE OR REFRESH MATERIALIZED VIEW fct_loan_quarantine')[1]
QUARANTINE_QUERY = 'SELECT ' + QUARANTINE.split('AS SELECT ')[1]


def delivery():
    with (UNIT / 'fixtures/nvb_edw.fct_loan.csv').open(newline='') as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter='|')]


def evaluate(rows):
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        fields = ', '.join(name + (' NUMERIC' if kind in ('INT', 'BIGINT') or
                                 kind.startswith('DECIMAL') else ' TEXT')
                           for name, kind in SCHEMA)
        db.execute('CREATE TABLE loan_candidates (' + fields + ')')
        db.executemany('INSERT INTO loan_candidates VALUES (' + ','.join('?' for _ in COLUMNS) + ')',
                       [[None if row[name] == r'\N' else row[name] for name in COLUMNS]
                        for row in rows])
        valid = [dict(row) for row in db.execute(
            'SELECT ' + PROJECTION + 'FROM loan_candidates WHERE ' + PREDICATE)]
        invalid = [dict(row) for row in db.execute(QUARANTINE_QUERY)]
        return valid, invalid


def metrics(rows):
    result = {'rows': len(rows), 'distinct': {}, 'sums': {}, 'dates': {}}
    for name, kind in SCHEMA:
        values = [row[name] for row in rows if row[name] is not None]
        if name.endswith('_id'):
            result['distinct'][name] = len(set(values))
        elif kind == 'INT' or kind.startswith('DECIMAL'):
            result['sums'][name] = float(sum((Decimal(str(v)) for v in values), Decimal(0)))
        elif name.endswith('_date') or name == 'load_ts':
            result['dates'][name] = {'min': min(values, default=None), 'max': max(values, default=None)}
        if name.endswith('_cd'):
            result[name + '_counts'] = dict(Counter(values))
    return result


def test_d0_control_metrics_and_immutable_fixtures():
    manifest = json.loads((ROOT / 'golden/manifest.json').read_text())
    for filename in ['nvb_edw.fct_loan.csv', 'nvb_edw.fct_loan.json']:
        entry = next(entry for entry in manifest['files'] if entry['path'].endswith('/' + filename))
        assert hashlib.sha256((UNIT / 'fixtures' / filename).read_bytes()).hexdigest() == entry['sha256']
    expected = json.loads((UNIT / 'fixtures/nvb_edw.fct_loan.json').read_text())
    valid, invalid = evaluate(delivery())
    assert invalid == []
    assert metrics(valid) == {key: value for key, value in expected.items()
                              if key not in ('table', 'input_rows')}


def test_schema_projection_and_registry():
    declaration = SQL.split('MATERIALIZED VIEW fct_loan (')[1].split('CONSTRAINT')[0]
    assert re.findall(r'(\w+)\s+(DECIMAL\(\d+,\d+\)|\w+)\s*,', declaration) == SCHEMA
    assert [part.strip() for part in PROJECTION.split(',')] == COLUMNS
    assert '${catalog}.${silver_schema}.asof_edw_fct_loan(:drop)' in SQL
    assert SQL.count("TBLPROPERTIES ('delta.feature.timestampNtz'='supported')") == 3
    assert 'CREATE OR REFRESH PRIVATE MATERIALIZED VIEW loan_candidates' in SQL
    assert 'TEMPORARY' not in SQL and '${drop}' not in SQL
    assert QUARANTINE.split('WHERE NOT (')[1].rsplit(');', 1)[0].strip() == PREDICATE.strip()
    spec = json.loads((UNIT / 'unit.json').read_text())
    assert spec == {'job': 'silver_pipeline', 'legacy_unit': 'foundation', 'outputs': [],
                    'owns': ['silver.fct_loan', 'silver.fct_loan_quarantine'],
                    'depends_on': ['silver_pipeline']}


def test_empty_delivery():
    assert evaluate([]) == ([], [])


@pytest.mark.parametrize('column,value', [
    ('batch_id', None), ('load_ts', None), ('record_status_cd', 'X'),
    ('contractual_rate', '-0.01'),
])
def test_rejected_rows_go_only_to_quarantine(column, value):
    good = delivery()[0]
    bad = dict(good, **{column: value})
    valid, invalid = evaluate([good, bad])
    assert len(valid) == len(invalid) == 1
    assert invalid[0][column] == (float(value) if column == 'contractual_rate' else value)


def test_nullable_checks_deleted_rows_and_multiset_are_preserved():
    row = dict(delivery()[0], loan_id=None, account_id=None, outstanding='0',
               arrears_days=None, record_status_cd='D', contractual_rate=None)
    valid, invalid = evaluate([row, row])
    assert len(valid) == 2 and invalid == []
    assert valid[0]['record_status_cd'] == 'D'
    assert Decimal(str(valid[0]['outstanding'])).quantize(Decimal('.01')) == Decimal('0.00')
    row['record_status_cd'] = None
    assert len(evaluate([row])[0]) == 1
