import json
import re
from pathlib import Path

import yaml

UNIT = Path(__file__).resolve().parent
ROOT = UNIT.parents[3]
SQL = (UNIT / 'close_batch.sql').read_text()
CHECK = (UNIT / 'check_close_batch.sql').read_text()
SPEC = json.loads((UNIT / 'unit.json').read_text())
JOB = yaml.safe_load((UNIT / 'close_batch.job.yml').read_text())['resources']['jobs']['close_batch']
CONTRACT = json.loads((ROOT / 'src/parity/contract.json').read_text())
ENTRY = next(e for e in CONTRACT['outputs'] if e['table'] == 'silver.etl_batch_control')


def table_columns():
    body = SQL.split(".etl_batch_control') (", 1)[1].split(');', 1)[0]
    return [tuple(part.split()) for part in body.replace('\n', ' ').split(',') if part.strip()]


def contract_columns():
    types = {'DATE': 'DATE', 'BIGINT': 'BIGINT'}
    return [(c['name'], 'STRING' if re.fullmatch(r'(VAR)?CHAR\(\d+\)', c['source_type'])
             else types[c['source_type']]) for c in ENTRY['columns']]


def test_registry_entry():
    assert SPEC == {'job': 'close_batch', 'legacy_unit': 'close_batch',
                    'outputs': ['silver.etl_batch_control'], 'owns': [],
                    'depends_on': ['silver_transactions', 'gold_channel']}
    assert ENTRY['unit'] == 'close_batch'


def test_table_matches_batch_open_contract_columns():
    assert table_columns() == contract_columns()
    assert 'load_ts' not in SQL


def test_control_check_precedes_write():
    assert SQL.index('raise_error') < SQL.index('MERGE INTO')
    assert SQL.index('mart_channel_daily') < SQL.index('MERGE INTO')
    assert 'OR m.payment_count IS NULL' in SQL
    assert "status = 'COMPLETE', last_step_cd = '10_close_batch', row_count = _close_count" in SQL


def test_no_delivery_literals():
    for text in (SQL, CHECK):
        assert '20250630' not in text and '2025-06-30' not in text and '2024-01-01' not in text


def test_job_definition():
    assert JOB['name'] == '${var.job_name_prefix}close_batch'
    params = {p['name']: p['default'] for p in JOB['parameters']}
    assert params == {'drop': 'D0', 'window_start': '2024-01-01'}
    tasks = {t['task_key']: t for t in JOB['tasks']}
    assert tasks['verify_close_batch']['depends_on'] == [{'task_key': 'close_batch'}]
    for task in tasks.values():
        sql_params = task['sql_task']['parameters']
        assert sql_params['catalog'] == '${var.catalog}'
        assert sql_params['silver_schema'] == '${var.silver_schema}'
