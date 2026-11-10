import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import full_job_verify as entry
import full_job_check as checker
from attest import render
from registry import load
from nightly_resources import VIEWS

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / 'src/parity/contract.json').read_text())


def complete_registry():
    registry = {'governance': dict(job='governance', legacy_unit='foundation', outputs=[], owns=[], depends_on=[])}
    for output in CONTRACT['outputs']:
        unit = output['unit']
        spec = registry.setdefault(unit, dict(job=unit, legacy_unit=unit, outputs=[], owns=[], depends_on=[]))
        spec['outputs'].append(output['table'])
    outputs = {entry['table'] for entry in CONTRACT['outputs']}
    views = {'gold.' + name for name in VIEWS}
    supporting = {table for requirements in VIEWS.values() for table in requirements} - outputs - views
    registry['supporting'] = dict(job='supporting', legacy_unit='foundation', outputs=[],
                                  owns=sorted(supporting), depends_on=[])
    return registry


def test_readiness_is_complete_not_partial_or_empty():
    checker.require_complete_registry(complete_registry(), CONTRACT)
    real = load(ROOT)
    missing = {entry['table'] for entry in CONTRACT['outputs']} - {
        table for spec in real.values() for table in spec['outputs']}
    if missing:
        with pytest.raises(ValueError, match='incomplete'):
            checker.require_complete_registry(real, CONTRACT)
    else:
        checker.require_complete_registry(real, CONTRACT)
    with pytest.raises(ValueError, match='incomplete'):
        checker.require_complete_registry({}, {'outputs': []})
    registry = complete_registry()
    del registry['governance']
    with pytest.raises(ValueError, match='requires governance'):
        checker.require_complete_registry(registry, CONTRACT)


def test_missing_supporting_table_owner_rejects_omitted_publish_gates():
    registry = complete_registry()
    registry['supporting']['owns'].remove('silver.dim_branch')
    with pytest.raises(ValueError, match='missing publish gates: .*v_credit_risk.*v_customer_portfolio'):
        checker.require_complete_registry(registry, CONTRACT)


def test_readiness_accepts_registered_view_publisher_instead_of_framework_tasks():
    registry = complete_registry()
    registry['views'] = dict(job='views', legacy_unit='foundation', outputs=[],
                             owns=['gold.' + name for name in VIEWS], depends_on=[])
    checker.require_complete_registry(registry, CONTRACT)


def test_readiness_rejects_duplicate_view_publishers(monkeypatch):
    registry = complete_registry()
    registry['views'] = dict(job='views', legacy_unit='foundation', outputs=[],
                             owns=['gold.v_credit_risk'], depends_on=[])
    build_job = checker.build_job

    def duplicate_publisher(*args, **kwargs):
        job = build_job(*args, **kwargs)
        job['tasks'].append(dict(task_key='v_credit_risk'))
        return job

    monkeypatch.setattr(checker, 'build_job', duplicate_publisher)
    with pytest.raises(ValueError, match='multiple publishers for v_credit_risk'):
        checker.require_complete_registry(registry, CONTRACT)


def test_readiness_rejects_two_registered_owners_of_a_view():
    registry = complete_registry()
    for name in ('first', 'second'):
        registry[name] = dict(job=name, legacy_unit='foundation', outputs=[],
                              owns=['gold.v_credit_risk'], depends_on=[])
    with pytest.raises(ValueError, match='owned by both'):
        checker.require_complete_registry(registry, CONTRACT)


def controls_fixture():
    files, data = [], {}
    baseline = dict(rows=2, input_rows={'nvb_edw.source': 2}, distinct={'id': 2}, sums={'amount': 3.5},
                    dates={'day_date': dict(min='2025-01-01', max='2025-01-02')}, category_counts={'A': 2})
    for table in ('nvb_edw.source', 'nvb_edw.item'):
        value = dict(baseline, table=table)
        if table.endswith('item'):
            value['dates'] = {**value['dates'], 'load_ts': dict(min='constant', max='constant')}
        data[f'controls/D0/expected/{table}.json'] = json.dumps(value).encode()
    data['controls/D0/summary.csv'] = b'table,rows\nnvb_edw.item,2\nnvb_edw.source,2\n'
    for path, content in data.items():
        files.append(dict(path=path, sha256=hashlib.sha256(content).hexdigest()))
    files += [dict(path='inputs/D0/nvb_edw.source/source.csv', table='nvb_edw.source', format='csv'),
              dict(path='outputs/D0/item/item.csv', source_table='nvb_edw.item', table='silver.item')]
    manifest = dict(files=files, excluded_from_parity=[dict(column='load_ts', tables=['nvb_edw.item'])])

    class Warehouse:
        def __init__(self):
            self.queries = []

        def sql(self, statement):
            self.queries.append(statement)
            if 'count(DISTINCT' in statement:
                return [['2']]
            if 'round(' in statement:
                return [['3.50']]
            if 'CAST(min(' in statement:
                return [['2025-01-01', '2025-01-02']]
            if 'GROUP BY' in statement:
                return [['A', '2']]
            return [['2']]

    return manifest, data, Warehouse()


def test_all_reference_controls_exact_and_dev_only():
    manifest, data, warehouse = controls_fixture()
    result = checker.compare_controls(manifest, data.__getitem__, warehouse, 'pr_42')
    assert len(result) == 3 and all(row['status'] == 'PASS' for row in result)
    assert all('`daiwt_01_mig_dev`.' in query for query in warehouse.queries)
    assert all(query.startswith('SELECT ') for query in warehouse.queries)
    assert not any('load_ts' in query for query in warehouse.queries)


@pytest.mark.parametrize('query_fragment,bad_result', [
    ('count(*) FROM', [['3']]), ('count(DISTINCT', [['1']]),
    ('round(', [['3.51']]), ('CAST(min(', [['2025-01-01', '2025-01-03']]),
    ('GROUP BY', [['A', '1']]),
])
def test_control_metric_mismatches_fail(query_fragment, bad_result):
    manifest, data, warehouse = controls_fixture()
    original = warehouse.sql
    warehouse.sql = lambda query: bad_result if query_fragment in query else original(query)
    result = checker.compare_controls(manifest, data.__getitem__, warehouse, 'pr_42')
    assert any(row['status'] == 'FAIL' for row in result)


def test_missing_controls_checksum_and_summary_fail_closed():
    with pytest.raises(ValueError, match='control files'):
        checker.compare_controls({'files': []}, lambda path: b'', None, 'pr_42')
    manifest, data, warehouse = controls_fixture()
    data['controls/D0/expected/nvb_edw.item.json'] = b'{}'
    data['controls/D0/summary.csv'] = b'table,rows\n'
    result = checker.compare_controls(manifest, data.__getitem__, warehouse, 'pr_42')
    assert sum(row['status'] == 'FAIL' for row in result) == 2
    assert all('checksum mismatch' in row['reason'] for row in result if row['status'] == 'FAIL')


@pytest.mark.parametrize('path,value', [
    ('controls/D0/summary.csv', b'table,rows\nnvb_edw.item,2\n'),
    ('controls/D0/expected/nvb_edw.item.json', b'{"table":"other"}'),
])
def test_valid_checksum_does_not_bypass_control_coverage(path, value):
    manifest, data, warehouse = controls_fixture()
    data[path] = value
    next(item for item in manifest['files'] if item['path'] == path)['sha256'] = hashlib.sha256(value).hexdigest()
    assert any(row['status'] == 'FAIL' for row in checker.compare_controls(manifest, data.__getitem__, warehouse, 'pr_42'))


@pytest.mark.parametrize('suffix', ['prod', 'pr_x', 'pr_1;bad'])
def test_full_checker_rejects_non_pr_suffix_before_remote_access(suffix):
    with pytest.raises(ValueError, match='dev PR suffix'):
        checker.run(None, suffix, 'warehouse', {}, CONTRACT)


def test_full_checker_combines_output_and_control_evidence(monkeypatch):
    base = dict(status='PASS', drop='D0', goldens=1, compared=1, counts=dict(PASS=1, FAIL=0, SKIP=0),
                results=[dict(table='silver.item', status='PASS', drop='D0')])
    monkeypatch.setattr(checker, 'parity_run', lambda *a, **kw: base)
    monkeypatch.setattr(checker, 'GoldenStore', lambda *a: SimpleNamespace(manifest=lambda: {}))
    monkeypatch.setattr(checker, 'compare_controls', lambda *a: [dict(table='controls/D0/summary.csv', status='FAIL', drop='D0')])
    result = checker.run(None, 'pr_42', 'warehouse', complete_registry(), CONTRACT)
    assert result['status'] == 'FAIL' and result['counts'] == dict(PASS=1, FAIL=1, SKIP=0)
    assert result['compared'] == result['goldens'] == 2
    assert not render(result)[1]
    base['results'][0]['status'] = 'SKIP'
    with pytest.raises(ValueError, match='no SKIP'):
        checker.run(None, 'pr_42', 'warehouse', complete_registry(), CONTRACT)


def test_visible_task_exits_nonzero_and_writes_failure_evidence(monkeypatch, tmp_path):
    output = tmp_path / 'result.json'
    monkeypatch.setattr('databricks.sdk.WorkspaceClient', lambda: object())
    monkeypatch.setattr(sys, 'argv', ['full_job_check.py', '--suffix', 'pr_42', '--output', str(output)])
    monkeypatch.setattr(checker, 'run', lambda *a: (_ for _ in ()).throw(RuntimeError('control mismatch')))
    with pytest.raises(RuntimeError, match='control mismatch'):
        checker.main()
    assert json.loads(output.read_text())['error'] == 'control mismatch'


def test_visible_task_success_returns_without_system_exit(monkeypatch, tmp_path):
    output = tmp_path / 'result.json'
    monkeypatch.setattr('databricks.sdk.WorkspaceClient', lambda: object())
    monkeypatch.setattr(sys, 'argv', ['full_job_check.py', '--suffix', 'pr_42', '--output', str(output)])
    result = dict(status='PASS', counts=dict(PASS=31, FAIL=0, SKIP=0))
    monkeypatch.setattr(checker, 'run', lambda *a: result)
    assert checker.main() is None
    assert json.loads(output.read_text()) == result
    source = (ROOT / 'src/parity/full_job_check.py').read_text()
    entry_point = source[source.rindex("if __name__ == '__main__':"):]
    exec(compile(entry_point, 'full_job_check.py', 'exec'), {'__name__': '__main__', 'main': checker.main})


def test_visible_verification_job_and_shell_entry_point():
    job = yaml.safe_load((ROOT / 'ci/full_job_verification.job.yml').read_text())['resources']['jobs']['full_job_verification']
    task = job['tasks'][0]
    assert task['task_key'] == 'verify_full_job'
    assert task['max_retries'] == 0
    assert (ROOT / 'ci' / task['spark_python_task']['python_file']).resolve() == ROOT / 'src/parity/full_job_check.py'
    assert '--suffix' in task['spark_python_task']['parameters']
    assert 'python ci/full_job_verify.py "$@"' in (ROOT / 'ci/full_job_verify.sh').read_text()


@pytest.mark.parametrize('fail_at', [None, 'deploy', 'nvb_nightly', 'full_job_verification', 'local-parity', 'record'])
def test_orchestration_order_failure_recording_and_no_catalog_override(monkeypatch, tmp_path, fail_at):
    (tmp_path / 'src/parity').mkdir(parents=True)
    (tmp_path / 'src/parity/contract.json').write_text(json.dumps(CONTRACT))
    monkeypatch.setattr(entry, 'ROOT', tmp_path)
    monkeypatch.setattr(entry, 'load', lambda root: complete_registry())
    calls = []

    def check_output(command, **kwargs):
        if command[1] == 'status':
            return ''
        return 'pushed-head'

    def run(command, check=False):
        calls.append(command)
        failed = ((fail_at == 'deploy' and 'deploy' in command)
                  or (fail_at == 'local-parity' and 'src/parity/full_job_check.py' in command)
                  or (fail_at == 'record' and 'ci/record_attestation.py' in command))
        if failed and check:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=int(failed))

    def run_job(suffix, key, urls, params=None):
        calls.append(['job', key, suffix, params])
        if fail_at == key:
            raise RuntimeError('job failed')

    monkeypatch.setattr(entry.subprocess, 'check_output', check_output)
    monkeypatch.setattr(entry.subprocess, 'run', run)
    monkeypatch.setattr(entry, 'run_job', run_job)
    assert entry.verify('pr_42') == (0 if fail_at is None else 1)
    record = next(command for command in calls if 'ci/record_attestation.py' in command)
    assert record[record.index('--status') + 1] == ('PASS' if fail_at in (None, 'record') else 'FAIL')
    assert record[record.index('--selection') + 1] == 'full-job'
    assert not any('catalog=' in str(command) for command in calls)
    assert json.loads((tmp_path / 'out/selected-units.json').read_text()) == sorted(complete_registry())
    if fail_at is None:
        jobs = [command for command in calls if command[0] == 'job']
        assert jobs == [['job', 'nvb_nightly', 'pr_42', 'drop=D0'], ['job', 'full_job_verification', 'pr_42', None]]


def test_run_job_uses_bundle_summary_and_records_urls(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(entry, 'deployed_jobs', lambda target, suffix: seen.append((target, suffix)) or {'nvb_nightly': {'id': '1'}})

    class Process:
        stdout = ['Run URL: https://example/run/1\n']
        returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self):
            return self.returncode

    commands = []
    monkeypatch.setattr(entry.subprocess, 'Popen', lambda command, **kw: commands.append(command) or Process())
    path = tmp_path / 'urls.txt'
    entry.run_job('pr_42', 'nvb_nightly', path, 'drop=D0')
    assert seen == [('dev', 'pr_42')]
    assert path.read_text() == 'Run URL: https://example/run/1\n'
    assert commands[0][-3:] == ['--params', 'drop=D0', 'nvb_nightly']
    with pytest.raises(ValueError, match='bundle summary'):
        entry.run_job('pr_42', 'absent', path)


@pytest.mark.parametrize('case,match', [('suffix', 'pr_<number>'), ('dirty', 'clean'), ('unpushed', 'remote branch')])
def test_entry_preconditions_prevent_deployment(monkeypatch, case, match):
    calls = []

    def check_output(command, **kwargs):
        if command[1] == 'status':
            return 'changed' if case == 'dirty' else ''
        return ''

    monkeypatch.setattr(entry.subprocess, 'check_output', check_output)
    monkeypatch.setattr(entry.subprocess, 'run', lambda command, **kw: calls.append(command))
    with pytest.raises(ValueError, match=match):
        entry.verify('prod' if case == 'suffix' else 'pr_42')
    assert not any(command[0] == 'databricks' for command in calls)
