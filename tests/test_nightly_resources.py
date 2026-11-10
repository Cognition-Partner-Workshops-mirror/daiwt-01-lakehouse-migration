import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from databricks.bundles.jobs import Job
from registry import load

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nightly_resources', ROOT / 'nightly_resources.py')
nightly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly)
MANIFEST = json.loads((ROOT / 'golden/manifest.json').read_text())
CONTRACT = json.loads((ROOT / 'src/parity/contract.json').read_text())


def unit(job, legacy='foundation', outputs=(), owns=(), deps=()):
    return dict(job=job, legacy_unit=legacy, outputs=list(outputs), owns=list(owns), depends_on=list(deps))


def foundation():
    return {
        'bronze_landing': unit('bronze_landing'),
        'sql_macros': unit('sql_macros'),
        'silver_asof': unit('silver_asof', deps=['bronze_landing']),
        'silver_pipeline': unit('silver_pipeline', deps=['silver_asof', 'sql_macros']),
    }


def full_registry():
    registry = foundation()
    previous = 'silver_pipeline'
    outputs = {entry['unit']: entry['table'] for entry in CONTRACT['outputs']}
    for index, legacy in enumerate(nightly.LEGACY_ORDER):
        job = 'silver_pipeline' if index < 3 else legacy
        name = 'silver_' + legacy if index < 3 else legacy
        registry[name] = unit(job, legacy, [outputs[legacy]], deps=[previous])
        previous = name
    registry['silver_inputs'] = unit('silver_pipeline', owns=[
        'silver.dim_branch', 'silver.dim_calendar', 'silver.fct_loan'], deps=['silver_pipeline'])
    return registry


def tasks(registry, manifest=MANIFEST):
    return {task['task_key']: task for task in nightly.build_job(registry, manifest, CONTRACT)['tasks']}


def deps(task):
    return {dependency['task_key'] for dependency in task.get('depends_on', [])}


def test_empty_and_foundation_only_registries():
    for registry in ({}, foundation()):
        generated = tasks(registry)
        assert set(generated) == {'batch_open', 'batch_fail', *(value['job'] for value in registry.values())}
        assert deps(generated['batch_open']) == set()
        for key, task in generated.items():
            assert task['max_retries'] == 0
            if key not in {'batch_open', 'batch_fail'}:
                assert 'batch_open' in deps(task)
                assert task['run_job_task'] == dict(job_id='${resources.jobs.' + key + '.id}',
                    job_parameters={'drop': '{{job.parameters.drop}}'})
        assert generated['batch_fail']['run_if'] == 'AT_LEAST_ONE_FAILED'
        assert deps(generated['batch_fail']) == set(generated) - {'batch_fail'}
    generated = tasks(foundation())
    assert {'silver_asof', 'sql_macros'} <= deps(generated['silver_pipeline'])


def test_legacy_chain_collapses_shared_jobs_and_preserves_registry_edges():
    registry = full_registry()
    generated = tasks(registry)
    assert sum(task.get('run_job_task', {}).get('job_id') == '${resources.jobs.silver_pipeline.id}'
               for task in generated.values()) == 1
    previous = 'silver_pipeline'
    for legacy in nightly.LEGACY_ORDER[3:]:
        assert previous in deps(generated[legacy])
        assert nightly.FOUNDATION <= deps(generated[legacy])
        previous = legacy
    for value in registry.values():
        expected = {registry[dep]['job'] for dep in value['depends_on']} - {value['job']}
        assert expected <= deps(generated[value['job']])


def test_chain_links_over_missing_steps_and_maps_renamed_jobs():
    registry = foundation()
    registry['first'] = unit('build_customer', 'customer', deps=['silver_pipeline'])
    registry['last'] = unit('finish_channel', 'channel', deps=['silver_pipeline'])
    generated = tasks(registry)
    assert 'build_customer' in deps(generated['finish_channel'])
    assert 'monthly_balance' not in generated


def test_governance_is_last_non_failure_task_and_waits_for_all():
    registry = full_registry()
    registry['governance'] = unit('governance')
    generated = tasks(registry)
    assert generated['governance']['run_if'] == 'NONE_FAILED'
    assert deps(generated['governance']) == set(generated) - {'governance', 'batch_fail'}
    assert 'governance' in deps(generated['batch_fail'])
    assert 'reconcile' in deps(generated['governance'])
    assert set(nightly.VIEWS) <= deps(generated['governance'])


def test_real_registry_publish_gates():
    registry = load(ROOT)
    generated = tasks(registry)
    assert generated['governance']['run_if'] == 'NONE_FAILED'
    assert deps(generated['governance']) == set(generated) - {'governance', 'batch_fail'}
    assert {spec['job'] for spec in registry.values()} <= set(generated)
    assert generated['batch_fail']['run_if'] == 'AT_LEAST_ONE_FAILED'
    assert deps(generated['batch_fail']) == set(generated) - {'batch_fail'}


def test_all_contract_units_and_publish_gates_precede_governance():
    registry = full_registry()
    registry['governance'] = unit('governance')
    assert {entry['unit'] for entry in CONTRACT['outputs']} == set(nightly.LEGACY_ORDER)
    assert {entry['table'] for entry in CONTRACT['outputs']} == {
        table for spec in registry.values() for table in spec['outputs']}
    generated = tasks(registry)
    gates = {spec['job'] for spec in registry.values()} - {'governance'}
    gates |= {'release_export', 'reconcile', *nightly.VIEWS}
    assert gates <= deps(generated['governance'])
    assert generated['governance']['run_if'] == 'NONE_FAILED'
    assert deps(generated['governance']) == set(generated) - {'governance', 'batch_fail'}
    assert generated['batch_fail']['run_if'] == 'AT_LEAST_ONE_FAILED'
    assert deps(generated['batch_fail']) == set(generated) - {'batch_fail'}


def test_conditional_controls_and_views_require_every_producer():
    registry = full_registry()
    generated = tasks(registry)
    assert {'close_batch', 'silver_pipeline'} <= deps(generated['release_export'])
    assert deps(generated['reconcile']) == {'release_export'}
    assert 'v_credit_risk' in deps(generated['report_risk_arrears'])
    assert 'reconcile' in deps(generated['v_customer_portfolio'])
    for requirements in nightly.VIEWS.values():
        for table in requirements:
            if table.startswith('gold.v_'):
                continue
            reduced = json.loads(json.dumps(registry))
            for value in reduced.values():
                value['outputs'] = [name for name in value['outputs'] if name != table]
                value['owns'] = [name for name in value['owns'] if name != table]
            reduced_tasks = tasks(reduced)
            for name, reads in nightly.VIEWS.items():
                if table in reads:
                    assert name not in reduced_tasks
    registry['close_batch']['outputs'] = []
    assert not {'release_export', 'reconcile'}.intersection(tasks(registry))


@pytest.mark.parametrize('field', ['owns', 'outputs'])
def test_registered_view_owner_replaces_framework_publisher_and_dependencies(field):
    registry = full_registry()
    registry['view_owner'] = unit('view_owner', **{field: ['gold.v_credit_risk']})
    registry['governance'] = unit('governance')
    generated = tasks(registry)
    assert 'v_credit_risk' not in generated
    assert 'view_owner' in deps(generated['report_risk_arrears'])
    assert 'v_credit_risk' not in deps(generated['report_risk_arrears'])
    assert set(nightly.VIEWS) - {'v_credit_risk'} <= set(generated)
    assert deps(generated['governance']) == set(generated) - {'governance', 'batch_fail'}
    assert generated['governance']['run_if'] == 'NONE_FAILED'


def test_unowned_views_keep_one_framework_publisher():
    generated = tasks(full_registry())
    for name in nightly.VIEWS:
        assert generated[name]['sql_task']['parameters']['view_sql'].startswith(
            'CREATE OR REPLACE VIEW {gold}.' + name)
    assert 'v_credit_risk' in deps(generated['report_risk_arrears'])


def test_real_registry_has_one_registered_business_view_publisher():
    registry = load(ROOT)
    generated = tasks(registry)
    assert not set(nightly.VIEWS) & set(generated)
    for name in nightly.VIEWS:
        assert [spec['job'] for spec in registry.values()
                if 'gold.' + name in spec['owns'] + spec['outputs']] == ['gold_business_views']
    assert 'gold_business_views' in deps(generated['governance'])


def test_registered_view_reads_match_declared_requirements():
    for name, requirements in nightly.VIEWS.items():
        sql = (ROOT / 'src/orchestration/views' / f'{name}.sql').read_text()
        reads = {f'{layer}.{table}' for layer, table in
                 re.findall(r'(?:FROM|JOIN)\s+\{(silver|gold)\}\.([a-z_]+)', sql)}
        assert reads == set(requirements)
        assert sql.startswith('CREATE OR REPLACE VIEW {gold}.' + name)
        assert 'TEMPORARY' not in sql and '2025-06-30' not in sql
    sql = (ROOT / 'src/orchestration/views/v_channel_trend.sql').read_text()
    assert 'ROWS BETWEEN 6 PRECEDING AND CURRENT ROW' in sql
    assert 'ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW' in sql


def test_settings_metadata_and_resource_loading():
    definition = nightly.build_job(foundation(), MANIFEST, CONTRACT)
    assert definition['name'] == '${var.job_name_prefix}nvb_nightly'
    assert definition['parameters'] == [{'name': 'drop', 'default': 'D0'}]
    assert definition['schedule'] == dict(quartz_cron_expression='0 30 0 * * ?',
        timezone_id='Europe/Oslo', pause_status='${var.nightly_pause_status}')
    assert definition['queue'] == {'enabled': True}
    assert definition['max_concurrent_runs'] == 1
    assert definition['health']['rules'] == [dict(metric='RUN_DURATION_SECONDS', op='GREATER_THAN', value=9000)]
    assert Job.from_dict(definition).as_dict()['tasks']
    resources = nightly.load_resources(None)
    assert set(resources.jobs) == {'nvb_nightly'}
    assert 'nvb_nightly' not in nightly.load(ROOT)
    manifest = json.loads(json.dumps(MANIFEST))
    manifest['drops'][0].update(name='D7', batch_id=20300101, business_date='2030-01-01')
    for entry in manifest['files']:
        entry['path'] = entry['path'].replace('/D0/', '/D7/')
    params = tasks(foundation(), manifest)['batch_open']['sql_task']['parameters']
    assert json.loads(params['manifest_drops'])['D7']['batch_id'] == 20300101


def test_generated_paths_are_bundle_relative():
    for task in nightly.build_job(full_registry(), MANIFEST, CONTRACT)['tasks']:
        if 'sql_task' in task:
            file = task['sql_task']['file']
            assert not Path(file['path']).is_absolute()
            assert (ROOT / file['path']).is_file()
            assert 'source' not in file
        if 'notebook_task' in task:
            notebook = task['notebook_task']
            assert not Path(notebook['notebook_path']).is_absolute()
            assert (ROOT / notebook['notebook_path']).is_file()
            assert 'source' not in notebook


def test_contract_columns_exact_order_and_no_extra_load_timestamp():
    with pytest.raises(ValueError, match='Missing batch-control contract'):
        nightly.control_columns({'outputs': []})
    columns = nightly.control_columns(CONTRACT)
    entry = next(value for value in CONTRACT['outputs'] if value['table'] == 'silver.etl_batch_control')
    assert [column.split()[0] for column in columns.split(', ')] == [column['name'] for column in entry['columns']]
    assert 'load_ts' not in columns
    assert 'batch_id BIGINT' in columns and 'status STRING' in columns
    invalid = json.loads(json.dumps(CONTRACT))
    next(value for value in invalid['outputs'] if value['unit'] == 'close_batch')['columns'][0]['source_type'] = 'BINARY'
    with pytest.raises(ValueError, match='Unsupported batch-control type'):
        nightly.control_columns(invalid)


def test_invalid_dags_and_reserved_keys_fail_closed():
    registry = foundation()
    registry['bad'] = unit('batch_open')
    with pytest.raises(ValueError, match='collides'):
        tasks(registry)
    registry = full_registry()
    registry['monthly_balance']['depends_on'].append('channel')
    with pytest.raises(ValueError, match='cycle'):
        tasks(registry)
    registry = foundation()
    registry['first'] = unit('first', 'customer')
    registry['second'] = unit('second', 'customer')
    with pytest.raises(ValueError, match='Multiple execution jobs'):
        tasks(registry)
    manifest = json.loads(json.dumps(MANIFEST))
    manifest['drops'][0]['batch_id'] = None
    with pytest.raises(ValueError, match='Incomplete drop metadata'):
        tasks(foundation(), manifest)


def test_control_sql_guards_retention_and_reconciliation():
    sql = {name: (ROOT / 'src/orchestration' / f'{name}.sql').read_text()
           for name in ('batch_open', 'release_export', 'reconcile', 'create_view')}
    for value in sql.values():
        assert 'from_json(:manifest_drops' in value and 'raise_error' in value
        assert '20250630' not in value and "DATE '2025-06-30'" not in value
    opening = sql['batch_open']
    assert opening.index("count_if(status = 'RUNNING')") < opening.index('MERGE INTO')
    assert "'OPEN'" in opening and "SET status = 'RUNNING'" in opening
    assert ':control_columns' in opening
    assert 'INTERVAL 400 DAYS' in sql['release_export']
    assert "'02_transactions'" in sql['release_export'] and "'nightly_release'" in sql['release_export']
    assert "c.status = 'COMPLETE'" in sql['reconcile']
    assert 'tx.net_amount <=> actual.net_amount' in sql['reconcile']
    assert 'raise_error' in sql['reconcile']
    assert 'EXECUTE IMMEDIATE replace' in sql['create_view']


@pytest.mark.parametrize('admitted', [0, 1])
def test_failure_handler_does_not_overwrite_a_rejected_running_batch(monkeypatch, admitted):
    import databricks.sdk
    calls = []
    values = dict(catalog='daiwt_01_mig_dev', silver_schema='silver_test', run_id='123',
                  drop='D0', manifest_drops=json.dumps(nightly.manifest_drops(MANIFEST)))
    widgets = SimpleNamespace(text=lambda *_: None, get=values.__getitem__)
    failed = SimpleNamespace(task_key='monthly_balance', start_time=1,
                             state=SimpleNamespace(result_state=SimpleNamespace(value='FAILED')))
    monkeypatch.setattr(databricks.sdk, 'WorkspaceClient', lambda: SimpleNamespace(
        jobs=SimpleNamespace(get_run=lambda **_: SimpleNamespace(tasks=[failed]))))

    class Spark:
        def sql(self, statement, args):
            calls.append((statement, args))
            return SimpleNamespace(first=lambda: {'n': admitted}, collect=lambda: [])

    namespace = dict(spark=Spark(), dbutils=SimpleNamespace(widgets=widgets))
    exec(compile((ROOT / 'src/orchestration/batch_fail.py').read_text(), 'batch_fail.py', 'exec'), namespace)
    assert len(calls) == (3 if admitted else 1)
    if admitted:
        assert calls[1][1]['step'] == 'monthly_balance'
        assert "SET status = 'FAILED', last_step_cd = :step" in calls[1][0]


def test_failure_handler_rejects_unknown_drop_before_api_or_writes():
    values = dict(catalog='daiwt_01_mig_dev', silver_schema='silver_test', run_id='123',
                  drop='UNKNOWN', manifest_drops='{}')
    widgets = SimpleNamespace(text=lambda *_: None, get=values.__getitem__)
    with pytest.raises(ValueError, match='Unknown captured drop'):
        exec(compile((ROOT / 'src/orchestration/batch_fail.py').read_text(), 'batch_fail.py', 'exec'),
             dict(dbutils=SimpleNamespace(widgets=widgets)))
