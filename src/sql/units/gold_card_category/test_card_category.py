import json
import re
import sqlite3
from pathlib import Path

import yaml

UNIT = Path(__file__).resolve().parent
SQL = (UNIT / 'card_category.sql').read_text()
CHECK = (UNIT / 'self_check.sql').read_text()
JOB = yaml.safe_load((UNIT / 'gold_card_category.job.yml').read_text())['resources']['jobs']['gold_card_category']
COLUMNS = ['category', 'txn_count', 'spend', 'batch_id', 'load_ts', 'src_system_cd',
           'record_status_cd', 'effective_date', 'expiry_date']
PARAMS = {'window_start': '2024-01-01', 'as_of_date': '2025-06-30', 'batch_id': '20250630'}


def candidate_blocks(text):
    return re.findall(r'-- mart candidate\n(.*?)\n\) AS k', text, re.S)


def to_sqlite(query):
    query = re.sub(r"IDENTIFIER\(:catalog \|\| '\.' \|\| :\w+ \|\| '\.(\w+)'\)", r'\1', query)
    return re.sub(r'CAST\((:\w+) AS DATE\)', r'\1', query)


def run(transactions, merchants):
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE fct_transaction (transaction_id, txn_date, amount, channel, merchant_id, record_status_cd)')
    db.execute('CREATE TABLE dim_merchant (merchant_id, category, record_status_cd)')
    db.executemany('INSERT INTO fct_transaction VALUES (?, ?, ?, ?, ?, ?)', transactions)
    db.executemany('INSERT INTO dim_merchant VALUES (?, ?, ?)', merchants)
    rows = db.execute(to_sqlite(candidate_blocks(SQL)[0]), PARAMS).fetchall()
    return {category: (count, round(spend, 2)) for category, count, spend in rows}


def test_unit_registry_entry():
    spec = json.loads((UNIT / 'unit.json').read_text())
    assert list(spec) == ['job', 'legacy_unit', 'outputs', 'owns', 'depends_on']
    assert spec == {'job': 'gold_card_category', 'legacy_unit': 'card_category',
                    'outputs': ['gold.mart_card_spend_category'], 'owns': [],
                    'depends_on': ['silver_transactions', 'silver_merchant']}


def test_job_parameters_and_verification_task():
    assert JOB['name'] == '${var.job_name_prefix}gold_card_category'
    assert {p['name']: p['default'] for p in JOB['parameters']} == {'drop': 'D0', **PARAMS}
    tasks = {task['task_key']: task for task in JOB['tasks']}
    assert list(tasks) == ['mart_card_spend_category', 'verify_mart_card_spend_category']
    assert tasks['verify_mart_card_spend_category']['depends_on'] == [{'task_key': 'mart_card_spend_category'}]
    for task in tasks.values():
        params = task['sql_task']['parameters']
        assert params['catalog'] == '${var.catalog}' and params['gold_schema'] == '${var.gold_schema}'
        assert all(params[name] == '{{job.parameters.%s}}' % name for name in ('drop', *PARAMS))
    assert 'daiwt_01' not in (UNIT / 'gold_card_category.job.yml').read_text()


def test_ddl_column_order_and_atomic_batch_replace():
    ddl = re.search(r"mart_card_spend_category'\) \((.*?)\n\)\nUSING DELTA", SQL, re.S)[1]
    assert [line.split()[0] for line in ddl.strip().splitlines()] == COLUMNS
    insert = SQL[SQL.index('INSERT INTO'):]
    assert 'REPLACE WHERE batch_id = CAST(:batch_id AS BIGINT)' in insert
    projected = re.findall(r'AS (\w+),?\n', insert.split('FROM (')[0])
    assert ['category', 'txn_count', 'spend'] + projected == COLUMNS
    assert SQL.count('DELETE') == 0 and SQL.index('raise_error') < SQL.index('INSERT INTO')


def test_candidate_is_shared_by_reconcile_and_insert():
    blocks = candidate_blocks(SQL)
    assert len(blocks) == 2 and blocks[0] == blocks[1]
    assert "coalesce(m.category, 'UNCLASSIFIED')" in blocks[0] and 'INNER JOIN' in blocks[0]
    assert 'dim_card' not in SQL


def test_control_aggregate_has_no_window_or_status_filter():
    controls = re.findall(r'SELECT m\.category, count\(\*\).*?GROUP BY m\.category', SQL + CHECK, re.S)
    assert len(controls) == 3
    for control in controls:
        assert 'record_status_cd' not in control and 'txn_date' not in control
        assert "t.channel = 'CARD' AND t.amount < 0" in control


def test_candidate_semantics():
    merchants = [(1, 'FUEL', 'A'), (2, 'GROCERY', 'A'), (3, None, 'A'), (4, 'TRAVEL', 'D')]
    transactions = [
        (1, '2025-01-01', -10.25, 'CARD', 1, 'A'),
        (2, '2025-06-30', -4.75, 'CARD', 1, 'A'),
        (3, '2024-01-01', -1.00, 'CARD', 2, 'A'),
        (4, '2025-01-01', 50.00, 'CARD', 2, 'A'),
        (5, '2025-01-01', -3.00, 'MOBILE', 2, 'A'),
        (6, '2023-12-31', -2.00, 'CARD', 2, 'A'),
        (7, '2025-07-01', -2.00, 'CARD', 2, 'A'),
        (8, '2025-01-01', -2.00, 'CARD', 2, 'D'),
        (9, '2025-01-01', -6.00, 'CARD', 3, 'A'),
        (10, '2025-01-01', -7.00, 'CARD', 4, 'A'),
        (11, '2025-01-01', -8.00, 'CARD', 99, 'A'),
    ]
    assert run(transactions, merchants) == {'FUEL': (2, 15.00), 'GROCERY': (1, 1.00), 'UNCLASSIFIED': (1, 6.00)}
