import json
import re
from pathlib import Path

import yaml

UNIT = Path(__file__).resolve().parent
ROOT = UNIT.parents[3]
SQL = (UNIT / 'product_profitability.sql').read_text()
JOB = yaml.safe_load((UNIT / 'product_profitability.job.yml').read_text())['resources']['jobs']


def test_unit_registry_entry():
    spec = json.loads((UNIT / 'unit.json').read_text())
    assert sorted(spec) == ['depends_on', 'job', 'legacy_unit', 'outputs', 'owns']
    assert spec['job'] == 'gold_product_profitability'
    assert spec['outputs'] == ['gold.mart_product_profitability']
    for dependency in spec['depends_on']:
        assert (ROOT / 'src/sql/units' / dependency / 'unit.json').exists()


def test_output_columns_match_contract_in_ddl_order():
    contract = json.loads((ROOT / 'src/parity/contract.json').read_text())
    entry = next(o for o in contract['outputs'] if o['table'] == 'gold.mart_product_profitability')
    select = SQL[SQL.index('CREATE OR REPLACE TABLE'):].split('AS SELECT', 1)[1].split('FROM vt_product_income')[0]
    columns = re.findall(r' AS (\w+),?\n', select)
    assert [c for c in columns if c != 'load_ts'] == [c['name'] for c in entry['columns']]
    assert columns.index('load_ts') == columns.index('batch_id') + 1


def test_legacy_arithmetic_and_controls():
    assert 'avg(CAST(b.balance AS DECIMAL(28,6))) AS precise_balance' in SQL
    assert 'max(p.annual_rate) AS annual_rate' in SQL
    assert 'CAST(precise_balance AS DECIMAL(18,2)) AS average_balance' in SQL
    assert 'CAST(precise_balance * annual_rate / 100 AS DECIMAL(18,2)) AS annual_income' in SQL
    assert 'annual_rate IS NULL OR annual_rate < 0 OR precise_balance IS NULL' in SQL
    assert SQL.index('raise_error') < SQL.index('CREATE OR REPLACE TABLE')
    assert 'bround' not in SQL.lower()
    assert not re.search(r"DATE '20(24|25)-", SQL)


def test_job_shape():
    job = JOB['gold_product_profitability']
    assert job['name'].startswith('${var.job_name_prefix}')
    params = {p['name']: p['default'] for p in job['parameters']}
    assert params == {'drop': 'D0', 'as_of_date': '2025-06-30', 'window_start': '2024-01-01',
                      'load_ts': '2025-06-30 23:00:00'}
    build, verify = job['tasks']
    assert build['sql_task']['parameters']['catalog'] == '${var.catalog}'
    assert verify['depends_on'] == [{'task_key': build['task_key']}]
    args = verify['spark_python_task']['parameters']
    assert args[args.index('--layer') + 1] == 'gold'
    assert (UNIT / verify['spark_python_task']['python_file']).resolve() == ROOT / 'src/parity/parity_check.py'
