import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / 'src/sql/units/sql_macros'
FUNCTIONS = (UNIT / 'functions.sql').read_text()
CHECK = (UNIT / 'self_check.sql').read_text()
README = (UNIT / 'README.md').read_text()
NAMES = ["f_td_add_months","f_month_start","f_month_end","f_decimal_2","f_zeroifnull","f_nullifzero","f_trim","f_clean_text","f_parse_amount"]


@pytest.mark.parametrize('name', NAMES)
def test_helpers_are_documented_and_self_checked(name):
    target = "IDENTIFIER(:catalog || '.' || :silver_schema || '." + name + "')"
    assert 'CREATE OR REPLACE FUNCTION ' + target in FUNCTIONS
    assert target + '(' in CHECK
    assert 'PASS ' + name in CHECK
    assert "raise_error('" + name + " semantics drift')" in CHECK
    assert name in README
    section = CHECK.split('THEN \'PASS ' + name + '\'')[0].rsplit('WITH cases(', 1)[1]
    assert 'CAST(NULL AS' in section
    assert 'count_if(NOT (actual <=> expected)) = 0' in section


def test_only_used_helpers_and_exact_semantics():
    assert FUNCTIONS.count('CREATE OR REPLACE FUNCTION') == len(NAMES)
    assert FUNCTIONS.count('DETERMINISTIC') == len(NAMES)
    assert 'RETURN add_months(d, n);' in FUNCTIONS
    assert "DATE '2024-12-30'" in CHECK and "DATE '2024-02-29'" in CHECK
    assert 'CAST(round(v, 2) AS DECIMAL(18,2))' in FUNCTIONS
    assert 'bround' not in FUNCTIONS and 'DOUBLE' not in FUNCTIONS
    assert 'CAST(-1.005 AS DECIMAL(38,6))' in CHECK
    assert "replace(s, chr(160), ' ')" in FUNCTIONS
    assert 'chr(39)' in CHECK
    assert "('bad', CAST(NULL AS DECIMAL(18,2)))" in CHECK
    assert re.search(r'legacy/.+?:[0-9]+', README)
    assert 'reports/card_services.sql:2' in README
    assert 'day-of-week' in README and 'no explicit ROUND' in README


def test_registry_and_job_tasks():
    spec = json.loads((UNIT / 'unit.json').read_text())
    assert spec['job'] == 'sql_macros'
    assert spec['outputs'] == spec['owns'] == spec['depends_on'] == []
    job = yaml.safe_load((UNIT / 'sql_macros.job.yml').read_text())['resources']['jobs']['sql_macros']
    assert job['name'] == '${var.job_name_prefix}sql_macros'
    assert job['parameters'] == [{'name': 'drop', 'default': 'D0'}]
    assert job['tasks'][1]['depends_on'] == [{'task_key': 'create_functions'}]
    for task, filename in zip(job['tasks'], ['functions.sql', 'self_check.sql']):
        assert task['sql_task']['file'] == {'path': './' + filename, 'source': 'WORKSPACE'}
        assert task['sql_task']['parameters']['silver_schema'] == '${var.silver_schema}'
        assert task['sql_task']['warehouse_id'] == '${var.warehouse_id}'
    assert 'permissions' not in job and 'run_as' not in job
