import json
from pathlib import Path

import yaml

UNIT = Path(__file__).resolve().parent
ROOT = UNIT.parents[3]
SPEC = json.loads((UNIT / 'unit.json').read_text())
SQL = (UNIT / 'channel_daily.sql').read_text()
JOB = yaml.safe_load((UNIT / 'gold_channel.job.yml').read_text())['resources']['jobs']['gold_channel']
CONTRACT = json.loads((ROOT / 'src/parity/contract.json').read_text())
ENTRY = next(e for e in CONTRACT['outputs'] if e['table'] == 'gold.mart_channel_daily')


def ddl_columns():
    body = SQL.split(".mart_channel_daily') (", 1)[1].split('\n)', 1)[0]
    return [line.strip().rstrip(',').split()[0] for line in body.strip().splitlines()]


def test_registry_entry():
    assert set(SPEC) == {'job', 'legacy_unit', 'outputs', 'owns', 'depends_on'}
    assert SPEC['legacy_unit'] == ENTRY['unit'] == 'channel'
    assert SPEC['outputs'] == ['gold.mart_channel_daily']
    for dependency in SPEC['depends_on']:
        assert (ROOT / 'src/sql/units' / dependency / 'unit.json').exists()


def test_columns_follow_legacy_ddl_order():
    contract = [c['name'] for c in ENTRY['columns']]
    columns = ddl_columns()
    assert [c for c in columns if c != 'load_ts'] == contract
    assert columns.index('load_ts') == columns.index('batch_id') + 1


def test_insert_lists_every_column_in_order():
    select = SQL.split('REPLACE WHERE', 1)[1].split('\nSELECT', 1)[1].split('FROM vt_channel_daily', 1)[0]
    lines = [line.strip().rstrip(',') for line in select.splitlines()[1:] if line.strip()]
    names = [line.split()[-1] for line in lines]
    assert names == ddl_columns()


def test_legacy_channel_rules():
    assert "coalesce(nullif(trim(t.channel), ''), 'ONLINE')" in SQL
    assert 'upper(' not in SQL.lower()
    assert "t.record_status_cd = 'A'" in SQL
    assert 'INNER JOIN' in SQL and SQL.count('raise_error(') == 2
    assert SQL.index('raise_error') < SQL.index('INSERT INTO')


def test_job_parameters_default_to_d0_literals():
    assert JOB['name'] == '${var.job_name_prefix}gold_channel'
    defaults = {p['name']: p['default'] for p in JOB['parameters']}
    assert defaults == {'drop': 'D0', 'as_of_date': '2025-06-30', 'window_start': '2024-01-01',
                        'batch_id': '20250630', 'load_ts': '2025-06-30 23:00:00'}
    assert [t['task_key'] for t in JOB['tasks']] == ['build_mart_channel_daily', 'verify_mart_channel_daily']
    for task in JOB['tasks']:
        assert task['sql_task']['parameters']['catalog'] == '${var.catalog}'
