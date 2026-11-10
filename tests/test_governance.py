import importlib.util
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from bronze_schema import bronze_tables
from classify_changes import classify
from registry import load

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / 'src/sql/units/governance'
spec = importlib.util.spec_from_file_location('governance', UNIT / 'governance.py')
governance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(governance)


def fixture():
    registry = {'producer': {'legacy_unit': 'old', 'outputs': ['silver.items', 'gold.report_old'],
                             'owns': ['bronze.edw_stage', 'gold.absent']}}
    contract = {'outputs': [dict(table=table, kind=kind, path=path,
                                columns=[{'name': 'id`x', 'source_type': "var'char\\type"}])
                            for table, kind, path in [('silver.items', 'table', "outputs/D0/a'b.csv"),
                                                      ('silver.items', 'table', 'outputs/D1/items.csv'),
                                                      ('gold.report_old', 'report', 'outputs/D0/report.csv')]]}
    schemas = {'bronze': 'b', 'silver': 's', 'gold': 'g'}
    existing = {('s', 'items'): {'table_type': 'MANAGED'},
                ('g', 'report_old'): {'table_type': 'VIEW'},
                ('b', 'edw_stage'): {'table_type': 'EXTERNAL'}}
    metadata = dict(business_owners={'old': 'Business Team', 'upstream': 'Upstream Team'},
                    framework={}, tables={
                        'nvb_edw.items': dict(comment="Legacy item's comment", columns={'id`x': 'Business key.'}),
                        'nvb_edw.stage': dict(comment='Raw source.', columns={'id': 'Raw key.'})})
    return registry, contract, 'cat`alog', schemas, 'o`wner', existing, metadata


def test_registry_contract_plan_and_escaping():
    table, report, owned, missing = governance.build_plan(*fixture())
    assert missing['status'] == 'SKIP' and missing['statements'] == []
    assert "Legacy item's comment" in table['comment']
    assert "outputs/D0/a'b.csv" in table['comment'] and 'outputs/D1/items.csv' in table['comment']
    assert owned['comment'].startswith('Raw source.')
    assert owned['tags']['parity_contract'] == 'no'
    assert owned['columns'] == {'id': 'Raw key.'}
    assert table['tags'] == dict(source_system='teradata', medallion_layer='silver',
                                migration_unit='producer', legacy_unit='old',
                                parity_contract='yes', business_owner='Business Team')
    sql = '\n'.join(statement for _, statement in table['statements'])
    assert '`cat``alog`.`s`.`items`' in sql and '`id``x`' in sql and '`o``wner`' in sql
    assert "a\\'b.csv" in sql and "var\\'char\\\\type" in sql
    assert "a''b" not in sql and 'GRANT' not in sql
    assert report['statements'][0][1].startswith('COMMENT ON VIEW')
    assert governance.literal('a\nb\rc\x00') == "'a\\nb\\rc\\0'"


@pytest.mark.parametrize('kind,form', [('MANAGED', 'TABLE'), ('EXTERNAL', 'TABLE'),
    ('VIEW', 'VIEW'), ('MATERIALIZED_VIEW', 'MATERIALIZED VIEW'), ('STREAMING_TABLE', 'STREAMING TABLE')])
def test_statement_forms(kind, form):
    args = fixture()
    args[5][('s', 'items')]['table_type'] = kind
    plan = governance.build_plan(*args)[0]
    sql = dict(plan['statements'])
    assert sql['comment'].startswith(f'COMMENT ON {form} ')
    assert sql['tags'].startswith(f'ALTER {form} ')
    assert sql['owner'].startswith(f'ALTER {form} ')


def test_empty_missing_and_invalid_contract():
    args = fixture()
    assert governance.build_plan({}, *args[1:]) == []
    args[5].clear()
    assert all(plan['status'] == 'SKIP' and not plan['statements']
               for plan in governance.build_plan(*args))
    args[1]['outputs'] = []
    with pytest.raises(ValueError, match='No contract'):
        governance.build_plan(*args)


def test_conflicting_contract_duplicate_ownership_and_landing_rejected():
    args = fixture()
    args[1]['outputs'][1]['columns'][0]['source_type'] = 'INTEGER'
    with pytest.raises(ValueError, match='Conflicting column contract'):
        governance.build_plan(*args)
    args = fixture()
    args[0]['another'] = args[0]['producer']
    with pytest.raises(ValueError, match='Duplicate governance target'):
        governance.build_plan(*args)
    args = fixture()
    args[3]['silver'] = 'landing'
    with pytest.raises(ValueError, match='landing'):
        governance.build_plan(*args)


def test_check_metadata_and_pipeline_exemptions():
    plan = governance.build_plan(*fixture())[0]
    key = (plan['schema'], plan['name'])
    tables = {key: {'comment': plan['comment'], 'table_owner': plan['owner']}}
    tags, columns = {key: plan['tags'].copy()}, {key: plan['columns'].copy()}
    assert governance.mismatches(plan, tables, tags, columns) == []
    tables[key]['comment'] = None
    tables[key]['table_owner'] = 'wrong'
    tags[key].pop('parity_contract')
    columns[key] = {}
    assert set(governance.mismatches(plan, tables, tags, columns)) == {
        'comment', 'owner', 'tag:parity_contract', 'column:id`x'}
    assert governance.mismatches(plan, tables, tags, columns,
                                 ['comment', 'owner', 'column:id`x']) == ['tag:parity_contract']


def test_empty_owner_preserves_existing_owner():
    args = list(fixture())
    args[4] = ''
    plan = governance.build_plan(*args)[0]
    assert 'owner' not in dict(plan['statements'])
    key = (plan['schema'], plan['name'])
    assert governance.mismatches(plan, {key: {'comment': plan['comment'], 'table_owner': 'unchanged'}},
                                  {key: plan['tags']}, {key: plan['columns']}) == []


def test_framework_targets_and_producer_priority():
    args = fixture()
    args[6]['framework'] = {'silver.framework_state': dict(legacy_unit='old', comment='Framework state.'),
                            'silver.items': dict(legacy_unit='upstream')}
    args[5][('s', 'framework_state')] = {'table_type': 'MANAGED'}
    plans = {plan['table']: plan for plan in governance.build_plan(*args)}
    assert plans['silver.items']['unit'] == 'producer'
    state = plans['silver.framework_state']
    assert state['unit'] == 'nvb_nightly' and state['status'] == 'APPLY'
    assert state['comment'].startswith('Framework state.')
    assert state['tags']['business_owner'] == 'Business Team'
    assert state['tags']['parity_contract'] == 'no'


def test_absent_legacy_columns_are_not_applied_to_non_contract_objects():
    args = fixture()
    plans = governance.build_plan(*args, existing_columns={('b', 'edw_stage'): {'other': None}})
    assert plans[2]['columns'] == {}
    assert plans[0]['columns']


def test_metadata_snapshot_schema_and_all_bronze_sources():
    registry, contract, metadata = governance.load_metadata(ROOT)
    assert registry == load(ROOT)
    for name, entry in metadata['tables'].items():
        assert name.count('.') == 1 and entry['source_file'].startswith('legacy/ddl/')
        assert entry['comment'] and all(isinstance(value, str) for value in entry['columns'].values())
    for name, columns in bronze_tables(ROOT).items():
        source = metadata['tables'][governance.legacy_table('bronze.' + name)]
        assert source['comment']
        assert set(source['columns']) <= {column['name'] for column in columns}
    views = {'gold.' + path.stem for path in (ROOT / 'src/orchestration/views').glob('*.sql')}
    assert views <= metadata['framework'].keys()
    for entry in metadata['framework'].values():
        assert entry['legacy_unit'] in metadata['business_owners']
    schemas = {layer: layer for layer in ('bronze', 'silver', 'gold')}
    plans = governance.build_plan(registry, contract, 'catalog', schemas, '', {}, metadata)
    targets = {table for spec in registry.values() for table in spec['outputs'] + spec['owns']}
    assert {plan['table'] for plan in plans} == targets | metadata['framework'].keys()
    for plan in plans:
        assert plan['status'] == 'SKIP'
        assert plan['tags']['source_system'] == 'teradata'


def test_framework_control_columns_follow_contract_not_excluded_legacy_columns():
    registry, contract, metadata = governance.load_metadata(ROOT)
    schemas = {layer: layer for layer in ('bronze', 'silver', 'gold')}
    plans = governance.build_plan(registry, contract, 'catalog', schemas, '', {}, metadata)
    for plan in plans:
        entries = [entry for entry in contract['outputs'] if entry['table'] == plan['table']]
        if entries:
            assert set(plan['columns']) == {column['name'] for entry in entries for column in entry['columns']}
            assert all('Legacy Teradata type:' in comment for comment in plan['columns'].values())


def test_job_serverless_safe_owner_and_unit_local_classification():
    job = yaml.safe_load((UNIT / 'governance.job.yml').read_text())['resources']['jobs']['governance']
    assert job['name'] == '${var.job_name_prefix}governance'
    assert {parameter['name']: parameter['default'] for parameter in job['parameters']} == {'drop': 'D0', 'data_owner': ''}
    assert [task['task_key'] for task in job['tasks']] == ['apply_governance', 'check_governance']
    assert job['tasks'][1]['depends_on'] == [{'task_key': 'apply_governance'}]
    for task in job['tasks']:
        assert not any('cluster' in key for key in task)
        assert task['notebook_task']['base_parameters']['bundle_root'] == '${workspace.file_path}'
        assert task['notebook_task']['base_parameters']['data_owner'] == '{{job.parameters.data_owner}}'
        assert (UNIT / task['notebook_task']['notebook_path']).is_file()
    registry = load(ROOT)
    assert registry['governance'] == dict(job='governance', legacy_unit='foundation', outputs=[], owns=[], depends_on=[])
    paths = [str(path.relative_to(ROOT)) for path in UNIT.iterdir() if path.is_file()]
    result = classify(registry, paths + ['tests/test_governance.py'])
    assert result == dict(required=True, units=['governance'], shared_changed=False)


def test_information_schema_reads_use_correct_schema_fields():
    class Row(dict):
        def asDict(self):
            return dict(self)

    class Spark:
        def __init__(self):
            self.queries = []

        def sql(self, query):
            self.queries.append(query)
            return self

        def collect(self):
            if len(self.queries) == 1:
                return [Row(table_schema='b', table_name='stage', comment='Table comment')]
            if len(self.queries) == 2:
                return [Row(schema_name='b', table_name='stage', tag_name='source', tag_value='teradata')]
            return [Row(table_schema='b', table_name='stage', column_name='id', comment='Key')]

    spark = Spark()
    tables, tags, columns = governance.read_state(spark, 'catalog', {'bronze': 'b'})
    assert 'table_schema IN' in spark.queries[0]
    assert 'schema_name IN' in spark.queries[1]
    assert 'table_schema IN' in spark.queries[2]
    assert tables[('b', 'stage')]['comment'] == 'Table comment'
    assert tags == {('b', 'stage'): {'source': 'teradata'}}
    assert columns == {('b', 'stage'): {'id': 'Key'}}


@pytest.mark.parametrize('pipeline,field,condition,exempt', [
    (True, 'comment', 'NOT_SUPPORTED', True),
    (True, 'column:id', 'UNSUPPORTED_OPERATION', True),
    (True, 'owner', 'UNSUPPORTED_OPERATION', True),
    (True, 'tags', 'NOT_SUPPORTED', False),
    (False, 'comment', 'NOT_SUPPORTED', False),
    (True, 'comment', 'PERMISSION_DENIED', False),
])
def test_apply_exempts_only_unsupported_pipeline_metadata(pipeline, field, condition, exempt):
    class SqlError(Exception):
        def getCondition(self):
            return condition

    class Spark:
        def sql(self, statement):
            raise SqlError('failure')

    plan = dict(table='silver.items', pipeline=pipeline, statements=[(field, 'SQL')])
    if exempt:
        assert governance.apply_plan(Spark(), plan) == [field]
    else:
        with pytest.raises(SqlError):
            governance.apply_plan(Spark(), plan)


@pytest.mark.parametrize('action,broken', [('apply', False), ('check', False), ('check', True), ('unknown', False)])
def test_notebook_flow_and_check_failures(monkeypatch, action, broken):
    args = fixture()
    plan = governance.build_plan(*args)[0]
    key = (plan['schema'], plan['name'])
    tables = {key: {'table_type': 'MANAGED', 'comment': None if broken else plan['comment'],
                    'table_owner': plan['owner']}}
    values = {'bundle_root': str(ROOT), 'catalog': args[2], 'data_owner': args[4], 'action': action,
              **{layer + '_schema': schema for layer, schema in args[3].items()}}
    saved = {}
    monkeypatch.setitem(sys.modules, 'governance', governance)
    monkeypatch.setattr(governance, 'load_metadata', lambda root: (args[0], args[1], args[6]))
    monkeypatch.setattr(governance, 'read_state', lambda *a: (tables, {key: plan['tags']}, {key: plan['columns']}))
    monkeypatch.setattr(sys, 'path', list(sys.path))
    dbutils = SimpleNamespace(widgets=SimpleNamespace(get=values.__getitem__), jobs=SimpleNamespace(
        taskValues=SimpleNamespace(set=lambda key, value: saved.update({key: value}), get=lambda **kw: {})))
    queries = []
    spark = SimpleNamespace(sql=lambda statement: (queries.append(statement) or SimpleNamespace(collect=lambda: [])))
    run = lambda: runpy.run_path(str(UNIT / 'governance_task.py'), init_globals={'dbutils': dbutils, 'spark': spark})
    if action == 'unknown':
        with pytest.raises(ValueError, match='Unknown action'):
            run()
    elif broken:
        with pytest.raises(RuntimeError, match='Governance check failed: silver.items: comment'):
            run()
    else:
        run()
        assert 'summary' in saved
        assert ('APPLIED' if action == 'apply' else 'PASS') in saved['summary']
    if action == 'check':
        assert queries == []
