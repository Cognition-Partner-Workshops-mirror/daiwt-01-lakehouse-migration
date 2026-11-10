import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'ci'))
import business_views_checks as checks
from generate_silver_asof import manifest_drops

spec = importlib.util.spec_from_file_location('nightly_resources', ROOT / 'nightly_resources.py')
nightly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly)
UNIT = json.loads((HERE / 'unit.json').read_text())
JOB = yaml.safe_load((HERE / 'gold_business_views.job.yml').read_text())['resources']['jobs']['gold_business_views']
TASKS = {task['task_key']: task for task in JOB['tasks']}
ARGS = ('daiwt_01_mig_dev', 'silver_pr_1', 'gold_pr_1', '2025-06-30', '2024-01-01', '2025')


def test_unit_json():
    assert set(UNIT) == {'job', 'legacy_unit', 'outputs', 'owns', 'depends_on'}
    assert UNIT['job'] == 'gold_business_views'
    reserved = {'batch_open', 'batch_fail', 'release_export', 'reconcile', *nightly.VIEWS}
    assert UNIT['job'] not in reserved
    assert set(checks.OBJECTS) == set(nightly.VIEWS)
    assert {'gold.' + name for name in checks.OBJECTS} == set(UNIT['owns'])
    assert UNIT['outputs'] == []


def test_job_uses_bundle_variables():
    assert JOB['name'] == '${var.job_name_prefix}gold_business_views'
    assert 'daiwt_01_mig' not in (HERE / 'gold_business_views.job.yml').read_text()
    defaults = {p['name']: p['default'] for p in JOB['parameters']}
    assert defaults['drop'] == 'D0'
    assert (defaults['as_of_date'], defaults['window_start'], defaults['report_year']) == ARGS[3:]
    manifest = json.loads((ROOT / 'golden/manifest.json').read_text())
    assert defaults['manifest_drops'] == json.dumps(manifest_drops(manifest), sort_keys=True)
    assert json.loads(defaults['manifest_drops'])['D0']['business_date'] == defaults['as_of_date']


@pytest.mark.parametrize('name', checks.OBJECTS)
def test_views_reuse_nightly_definition(name):
    task = TASKS[name]['sql_task']
    assert (HERE / task['file']['path']).resolve() == ROOT / 'src/orchestration/create_view.sql'
    assert task['parameters']['view_sql'] == (ROOT / 'src/orchestration/views' / f'{name}.sql').read_text()
    assert task['parameters']['catalog'] == '${var.catalog}'
    assert task['parameters']['gold_schema'] == '${var.gold_schema}'


def test_reconciliation_waits_for_every_object():
    verify = TASKS['reconcile_business_views']
    assert {dep['task_key'] for dep in verify['depends_on']} == set(checks.OBJECTS)
    assert [dep['task_key'] for dep in TASKS['report_risk_arrears']['depends_on']] == ['v_credit_risk']
    assert (HERE / verify['notebook_task']['notebook_path']).exists()


def test_legacy_translation():
    sql = checks.legacy_sql(*ARGS)
    assert set(sql) == set(checks.OBJECTS)
    trend = sql['v_channel_trend']
    assert "BETWEEN DATE '2024-01-01' AND DATE '2025-06-30'" in trend
    assert 'ROWS UNBOUNDED PRECEDING' in trend and 'ROWS 6 PRECEDING' in trend
    assert "m.channel = 'MOBILE'" in trend
    assert 'EXTRACT(YEAR FROM month_start) = 2025' in sql['report_finance_monthly']
    assert 'spend / nullif(SUM(spend) OVER (), 0)' in sql['report_card_services']
    assert sql['v_credit_risk'] in sql['report_risk_arrears']
    assert '`daiwt_01_mig_dev`.`gold_pr_1`.`mart_customer_segment`' in sql['v_customer_portfolio']
    assert all('ORDER BY' not in text.replace('OVER (ORDER BY', '') for text in sql.values())


@pytest.mark.parametrize('call', [
    lambda: checks.quote('bad-name'),
    lambda: checks.quote('a`b'),
    lambda: checks.date_literal("2025-06-30' OR 1=1"),
    lambda: checks.date_literal('2025-02-30'),
    lambda: checks.year_literal('20x5'),
])
def test_rejects_unsafe_arguments(call):
    with pytest.raises(ValueError):
        call()


def fake(extra=0, bands=None, as_of='2025-06-30'):
    expectations = checks.load_expectations('D0')
    rows = expectations['rows']
    bands = bands or expectations['v_credit_risk_bands']

    def query(sql):
        if 'EXCEPT ALL' in sql:
            name = next(n for n in checks.OBJECTS if f'`{n}`)' in sql)
            return [(str(rows[name] + extra), str(rows[name]), str(extra), '0')]
        if 'GROUP BY risk_band' in sql:
            return list(bands.items())
        if sql.startswith('SELECT COUNT(*) FROM'):
            return [(rows[sql.rsplit('`', 2)[1]],)]
        return [('42',)]

    def schema(sql):
        return [('customer_id', 'int')]

    drops = json.dumps({'D0': {'batch_id': 20250630, 'business_date': '2025-06-30', 'counts': {}}})
    return checks.run(query, schema, *ARGS[:3], 'D0', drops, as_of, *ARGS[4:])


def test_run_passes_when_everything_reconciles():
    results = fake()
    assert results and all(r['status'] == 'PASS' for r in results)
    assert '/' in checks.markdown(results)


def test_run_fails_on_unexpected_row_band_or_date():
    assert any(r['status'] == 'FAIL' and 'multiset' in r['check'] for r in fake(extra=1))
    assert any(r['status'] == 'FAIL' and 'IMPAIRED' in r['check']
               for r in fake(bands={'IMPAIRED': 1, 'WATCH': 314, 'PERFORMING': 1157}))
    assert any(r['status'] == 'FAIL' and 'business_date' in r['check'] for r in fake(as_of='2025-06-29'))


def test_unknown_drop():
    with pytest.raises(ValueError, match='Unknown captured drop'):
        checks.run(None, None, *ARGS[:3], 'D9', '{}', *ARGS[3:])
