import json
import subprocess
from pathlib import Path

import pytest
import yaml

from databricks.bundles.jobs import Job

from registry import load, validate, with_dependencies
from run_units import execution_jobs, run_units
from test_silver_resources import loader, module_loader

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / 'src/sql/units/silver_pipeline'


def test_zero_entities_verify_only(tmp_path):
    module = module_loader(tmp_path)
    resources = module.load_resources(None)
    assert resources.pipelines == {} and resources.jobs == {}
    job = yaml.safe_load((UNIT / 'silver_pipeline.job.yml').read_text())['resources']['jobs']['silver_pipeline']
    assert [t['task_key'] for t in job['tasks']] == ['verify_silver']
    task = job['tasks'][0]
    assert 'pipeline_task' not in task and 'depends_on' not in task
    args = task['spark_python_task']['parameters']
    assert args[args.index('--layer') + 1] == 'silver'
    assert args[args.index('--drop') + 1] == '{{job.parameters.drop}}'
    assert task['environment_key'] == 'parity'
    definition = Job.from_dict(job)
    assert module.configure_silver_job.function(None, definition) is definition


def test_entity_adds_refresh_and_named_drop(tmp_path):
    module = module_loader(tmp_path)
    entity = tmp_path / 'src/sql/units/silver_example'
    entity.mkdir()
    (entity / 'example.pipeline.sql').write_text('CREATE OR REFRESH MATERIALIZED VIEW example AS SELECT 1 AS id;')
    resources = module.load_resources(None)
    pipeline = resources.pipelines['silver'].as_dict()
    assert pipeline['parameters'] == {'drop': 'D0'}
    assert pipeline['configuration']['bronze_schema'] == '${var.bronze_schema}'
    template = yaml.safe_load((UNIT / 'silver_pipeline.job.yml').read_text())['resources']['jobs']['silver_pipeline']
    job = module.configure_silver_job.function(None, Job.from_dict(template)).as_dict()
    assert job['max_concurrent_runs'] == 1
    tasks = {task['task_key']: task for task in job['tasks']}
    python_file = tasks['verify_silver']['spark_python_task']['python_file']
    assert python_file == 'src/parity/parity_check.py'
    assert not Path(python_file).is_absolute()
    refresh = tasks['refresh_silver']['pipeline_task']
    assert refresh == dict(pipeline_id='${resources.pipelines.silver.id}', full_refresh=True,
                           parameters={'drop': '{{job.parameters.drop}}'})
    assert tasks['verify_silver']['depends_on'] == [{'task_key': 'refresh_silver'}]
    unrelated = Job.from_dict({**template, 'tags': {}})
    assert module.configure_silver_job.function(None, unrelated) is unrelated


def entity_spec():
    return dict(job='silver_pipeline', legacy_unit='customer', outputs=[], owns=[], depends_on=['silver_pipeline'])


def test_entity_selection_and_shared_job_runs_once(monkeypatch):
    registry = load(ROOT)
    registry.update(silver_one=entity_spec(), silver_two=entity_spec())
    validate(registry, json.loads((ROOT / 'src/parity/contract.json').read_text()))
    selected = with_dependencies(registry, ['silver_one', 'silver_two'])
    assert set(selected) == {'bronze_landing', 'silver_asof', 'sql_macros', 'silver_pipeline', 'silver_one', 'silver_two'}
    calls = []
    class Process:
        stdout = []
        returncode = 0
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def wait(self):
            return self.returncode
    monkeypatch.setattr(subprocess, 'Popen', lambda command, **kw: calls.append(command) or Process())
    monkeypatch.setattr('run_units.deployed_jobs', lambda *args: {s['job']: {'id': '1'} for s in registry.values()})
    run_units('pr_1', registry, selected, drop='D2')
    assert [call[-1] for call in calls] == ['bronze_landing', 'silver_asof', 'sql_macros', 'silver_pipeline']
    assert all('drop=D2' in call for call in calls)
    calls.clear()
    run_units('pr_1', registry, [], drop='D0')
    assert calls == []
    Process.returncode = 1
    with pytest.raises(subprocess.CalledProcessError):
        run_units('pr_1', registry, ['silver_one', 'silver_two'], drop='D0')
    assert len(calls) == 1


@pytest.mark.parametrize('name,deps', [('gold_bad', ['silver_pipeline']), ('silver_bad', [])])
def test_invalid_shared_job(name, deps):
    registry = load(ROOT)
    registry[name] = {**entity_spec(), 'depends_on': deps}
    with pytest.raises(ValueError, match='shared silver_pipeline'):
        validate(registry, json.loads((ROOT / 'src/parity/contract.json').read_text()))


def test_shared_job_waits_for_entity_specific_dependencies():
    registry = {
        'silver_pipeline': dict(job='silver_pipeline', depends_on=[]),
        'silver_entity': dict(job='silver_pipeline', depends_on=['silver_pipeline', 'z_helper']),
        'z_helper': dict(job='helper', depends_on=[]),
    }
    assert execution_jobs(registry, ['silver_pipeline', 'z_helper', 'silver_entity']) == ['helper', 'silver_pipeline']
    registry['z_helper']['depends_on'] = ['silver_pipeline']
    with pytest.raises(ValueError, match='cycle between shared jobs'):
        execution_jobs(registry, ['silver_pipeline', 'z_helper', 'silver_entity'])


def test_scaffold_contract_and_builder_docs():
    spec = json.loads((UNIT / 'unit.json').read_text())
    assert spec['legacy_unit'] == 'foundation'
    assert spec['depends_on'] == ['silver_asof', 'sql_macros']
    assert spec['outputs'] == spec['owns'] == []
    doc = (UNIT / 'README.md').read_text()
    for text in ['PRIVATE MATERIALIZED VIEW', 'never TEMPORARY VIEW', 'timestampNtz',
                 ':drop', '${bronze_schema}', 'verify-only', 'once', 'job: silver_pipeline']:
        assert text in doc
