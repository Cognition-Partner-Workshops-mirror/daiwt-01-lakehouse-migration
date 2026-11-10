"""Offline policy tests use real commits, including real branch rebases."""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from check_attestation import check, evaluate
from classify_changes import classify_diff, fingerprint
from registry import load, load_at, validate
from parity.parity_check import compare

CONTRACT = {'outputs': [{'table': 'silver.customers', 'unit': 'foundation'},
                        {'table': 'gold.sales', 'unit': 'sales'}]}


def spec(name, deps=()):
    return dict(job=name, legacy_unit='foundation', outputs=[], owns=[], depends_on=list(deps))


def write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if isinstance(content, str) else json.dumps(content))


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def commit(message):
    git('add', '-A')
    git('commit', '-qm', message)
    return git('rev-parse', 'HEAD')


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git('init', '-q', '-b', 'main')
    # Command-local identity avoids changing any Git configuration.
    monkeypatch.setenv('GIT_AUTHOR_NAME', 'Policy tests')
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 'policy@example.invalid')
    monkeypatch.setenv('GIT_COMMITTER_NAME', 'Policy tests')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL', 'policy@example.invalid')
    write(tmp_path, 'src/parity/contract.json', CONTRACT)
    write(tmp_path, 'ci/shared.py', 'shared = 1\n')
    for name, deps in [('a', []), ('b', ['a']), ('c', ['b'])]:
        write(tmp_path, f'src/sql/units/{name}/unit.json', spec(name, deps))
        write(tmp_path, f'src/sql/units/{name}/query.sql', 'SELECT 1;\n')
    commit('baseline')
    return tmp_path


@pytest.mark.parametrize('path,units,shared', [
    ('src/sql/units/c/query.sql', ['a', 'b', 'c'], False),
    ('src/sql/units/a/query.sql', ['a'], False),
    ('src/sql/units/b/b.job.yml', ['a', 'b'], False),
    ('ci/shared.py', ['a', 'b', 'c'], True),
    ('README.md', [], False), ('docs/guide.md', [], False),
    ('tests/test_new.py', [], False), ('.github/workflows/pr.yml', [], False),
    ('.gitignore', [], False), ('AGENTS.md', [], False)])
def test_diff_selection(repo, path, units, shared):
    base = git('rev-parse', 'HEAD')
    write(repo, path, 'changed\n')
    head = commit('change')
    assert classify_diff(base, head) == dict(required=shared or bool(units), units=units, shared_changed=shared)


def test_add_remove_unchanged(repo):
    base = git('rev-parse', 'HEAD')
    assert not classify_diff(base, base)['required']
    write(repo, 'src/sql/units/d/unit.json', spec('d', ['b']))
    added = commit('added')
    assert classify_diff(base, added)['units'] == ['a', 'b', 'd']
    git('rm', '-r', 'src/sql/units/d')
    removed = commit('removed')
    assert not classify_diff(added, removed)['required']
    assert load_at(removed) == load(repo)


@pytest.mark.parametrize('main_change,stable', [('unrelated', True), ('shared', False), ('upstream', False)])
def test_rebase_fingerprint(repo, main_change, stable):
    git('switch', '-qc', 'unit-branch')
    write(repo, 'src/sql/units/c/query.sql', 'SELECT 2;\n')
    branch = commit('unit change')
    before = fingerprint(branch, ['c'])
    git('switch', '-q', 'main')
    if main_change == 'unrelated':
        write(repo, 'src/sql/units/unrelated/unit.json', spec('unrelated'))
        write(repo, 'docs/new.md', 'new docs')
    elif main_change == 'shared':
        write(repo, 'ci/shared.py', 'shared = 2\n')
    else:
        write(repo, 'src/sql/units/a/query.sql', 'SELECT 3;\n')
    commit('main change')
    git('switch', '-q', 'unit-branch')
    git('rebase', 'main')
    assert (fingerprint('HEAD', ['c']) == before) is stable
    assert fingerprint('HEAD', ['c']) == fingerprint('HEAD', ['a', 'b', 'c'])
    assert classify_diff('main', 'HEAD')['units'] == ['a', 'b', 'c']


def evidence(**values):
    return dict(status='PASS', units='["a", "b", "c"]', fingerprint='current',
                drops='{"D0":{"status":"PASS"}}', attestation_md='# evidence', **values)


@pytest.mark.parametrize('patch,ok', [({}, True), ({'fingerprint': 'stale'}, False),
    ({'units': '["a"]'}, False), ({'status': 'FAIL'}, False),
    ({'drops': '{"D0":{"status":"FAIL"}}'}, False),
    ({'drops': '{}'}, False), ({'units': 'null'}, False), ({'drops': 'bad'}, False)])
def test_attestation_evaluate(patch, ok):
    row = evidence()
    row.update(patch)
    accepted, _ = evaluate([row], 'head', ['b'], lambda rev, units: 'current')
    assert bool(accepted) is ok


def test_accept_first_valid_row_and_no_rows():
    bad = evidence()
    bad['fingerprint'] = 'stale'
    accepted, reason = evaluate([bad, evidence()], 'head', ['a'], lambda *_: 'current')
    assert accepted and reason == 'PASS'
    assert evaluate([], 'head', [], lambda *_: 'current')[0] is None


def test_newest_suffix_failure_overrides_earlier_pass():
    earlier = evidence(suffix='pr_1', recorded_at='2026-01-01T00:00:00Z')
    later = {**earlier, 'status': 'FAIL', 'recorded_at': '2026-01-01T00:01:00Z'}
    other = evidence(suffix='pr_2', recorded_at='2026-01-01T00:00:30Z')
    rows = [later, other, earlier]
    accepted, reason = evaluate(rows, 'head', ['a'], lambda *_: 'current', suffix='pr_1')
    assert accepted is None and 'newest attestation for pr_1 is FAIL' in reason
    ok, body = check('head', 'base', 'pr_1', lambda *_: rows, lambda *_: 'current',
                     classify_fn=lambda *_: {'required': True, 'units': ['a']},
                     registry_fn=lambda _: {'a': {'outputs': ['gold.sales']}}, timeout=1800,
                     sleep=lambda _: pytest.fail('known failed evidence must fail immediately'))
    assert not ok and 'newest attestation for pr_1 is FAIL' in body


def test_matching_fingerprint_pass_from_other_suffix_is_reusable():
    row = evidence(suffix='pr_2')
    accepted, reason = evaluate([row], 'head', ['a'], lambda *_: 'current', suffix='pr_1')
    assert accepted == row and reason == 'PASS'
    older_failure = {**row, 'suffix': 'pr_1', 'status': 'FAIL'}
    newer_pass = {**row, 'suffix': 'pr_1'}
    accepted, reason = evaluate([newer_pass, older_failure], 'head', ['a'], lambda *_: 'current', suffix='pr_1')
    assert accepted == newer_pass and reason == 'PASS'


def test_query_keeps_all_statuses_for_current_suffix():
    import inspect
    import check_attestation
    source = inspect.getsource(check_attestation.main)
    assert "WHERE suffix = :suffix OR (status = 'PASS' AND fingerprint = :fp)" in source
    assert 'ORDER BY recorded_at DESC' in source


def test_injectable_check_and_no_query_for_ignored():
    calls = []
    def query(fp, suffix):
        calls.append((fp, suffix))
        return [evidence()]
    classify = lambda *_: dict(required=True, units=['a'], shared_changed=False)
    assert check('head', 'base', 'pr_1', query, lambda *_: 'current', classify, registry_fn=lambda _: {'a': {'outputs': ['gold.sales']}}) == (True, '# evidence')
    assert calls == [('current', 'pr_1')]
    ok, body = check('head', 'base', 'pr_1', lambda *_: [], lambda *_: 'current', classify, registry_fn=lambda _: {'a': {'outputs': ['gold.sales']}})
    assert not ok and "bash ci/dev_verify.sh pr_1 'a'" in body and 'Required units: ["a"]' in body
    assert check('h', 'b', 'pr_1', None, classify_fn=lambda *_: {'required': False}) == (
        True, 'No attestation required (docs/tests only)')


def test_unit_directory_schema(repo):
    unit = spec('customers')
    unit['outputs'] = ['silver.customers']
    write(repo, 'src/sql/units/customers/unit.json', unit)
    loaded = load(repo)
    assert list(loaded) == ['a', 'b', 'c', 'customers']
    assert loaded['customers']['legacy_unit'] == 'foundation'
    for patch, reason in [({'legacy_unit': 'missing'}, 'legacy_unit'),
                          ({'outputs': ['gold.sales']}, 'another legacy_unit'),
                          ({'owns': ['silver.customers']}, 'belongs in outputs')]:
        invalid = {**unit, **patch}
        with pytest.raises(ValueError, match=reason):
            validate({'customers': invalid}, CONTRACT)
    write(repo, 'src/sql/units/missing/query.sql', 'SELECT 1')
    with pytest.raises(FileNotFoundError):
        load(repo)
    commit('missing registry')
    with pytest.raises(subprocess.CalledProcessError):
        load_at('HEAD')


def test_parity_producer_mapping_and_selection():
    entries = [{'table': 'silver.customers', 'unit': 'foundation'},
               {'table': 'gold.sales', 'unit': 'sales'}]
    registry = {'silver_customers': {**spec('customers'), 'outputs': ['silver.customers']}}
    result = compare({'tables': entries}, registry, None, None, ran_units=[])
    assert [row['reason'] for row in result['results']] == ['not selected', 'not yet migrated']
    result = compare({'tables': entries}, registry, lambda _: [], lambda _: [], ran_units=['silver_customers'])
    assert result['results'][0]['status'] == 'PASS'
    assert result['results'][0]['unit'] == 'silver_customers'
    # A legacy-name match alone must not make an unowned table migrated.
    assert compare({'tables': entries}, {'foundation': {'outputs': []}}, None, None)['results'][0]['reason'] == 'not yet migrated'


@pytest.mark.parametrize('valid,expected', [(True, 'PASS'), (False, 'FAIL')])
def test_record_d0_fingerprint_offline(repo, monkeypatch, valid, expected):
    import databricks.sdk
    import record_attestation as recorder
    from test_ci import parity_result
    calls = []
    monkeypatch.setattr(databricks.sdk, 'WorkspaceClient', lambda: SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name='builder'))))
    class Warehouse:
        def __init__(self, *args):
            pass

        def sql(self, sql, params=None):
            calls.append((sql, params))
    monkeypatch.setattr(recorder, 'Warehouse', Warehouse)
    result = parity_result('D0') if valid else {'status': 'FAIL'}
    write(repo, 'out/parity-D0.json', result)
    write(repo, 'out/parity-D1.json', {'status': 'FAIL'})
    write(repo, 'out/selected-units.json', ['a', 'b'])
    write(repo, 'out/run-urls.txt', 'https://example/run/1\n')
    write(repo, 'attestation.md', '# evidence')
    monkeypatch.setattr(sys, 'argv', ['record_attestation', '--head-sha', 'HEAD',
        '--suffix', 'pr_1', '--status', 'PASS', '--units', 'out/selected-units.json', '--selection', 'explicit'])
    assert recorder.main() == (0 if valid else 1)
    assert 'unit_attestation' in calls[0][0]
    params = calls[1][1]
    assert params['status'] == expected
    assert set(json.loads(params['drops'])) == {'D0'}
    assert params['fingerprint'] == fingerprint('HEAD', ['a', 'b'])
    assert params['recorded_by'] == 'builder'


@pytest.mark.parametrize("units,registry", [([], {}), (["a"], {"a": {"outputs": []}})])
def test_no_golden_selection_skips_without_query(units, registry):
    def unexpected(*args):
        raise AssertionError("must not query or fingerprint")
    ok, body = check("head", "base", "pr_1", unexpected, unexpected,
                     lambda *_: dict(required=True, units=units), registry_fn=lambda _: registry,
                     timeout=1800, sleep=unexpected)
    assert ok and "no golden outputs" in body


@pytest.mark.parametrize("kind", ["late", "missing", "error"])
def test_polling_bounded_at_thirty_minutes(kind):
    now, calls = [0], []
    def query(*args):
        calls.append(now[0])
        if kind == "error":
            raise RuntimeError("unavailable")
        return [evidence()] if kind == "late" and now[0] >= 60 else []
    def sleep(seconds):
        now[0] += seconds
    ok, body = check("head", "base", "pr_1", query, lambda *_: "current",
        lambda *_: dict(required=True, units=["a"]),
        registry_fn=lambda _: {"a": {"outputs": ["gold.sales"]}},
        timeout=1800, clock=lambda: now[0], sleep=sleep)
    assert ok is (kind == "late")
    assert now[0] == (60 if kind == "late" else 1800)
    assert calls[0] == 0 and calls[-1] == now[0]
    if kind == "error":
        assert "unavailable" in body


def test_workflow_and_visible_task_policy():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[1]
    pr = (root / ".github/workflows/pr.yml").read_text()
    assert "python -m pytest -q" in pr
    for target in ("dev", "prod"):
        assert f"bundle validate --strict -t {target}" in pr
    assert "check_attestation.py" in pr
    assert "bundle deploy" not in pr and "bundle run" not in pr
    assert "timeout=1800" in (root / "ci/check_attestation.py").read_text()
    assert '"bundle", "summary"' in (root / "ci/run_units.py").read_text()
    for path in (root / ".github/workflows").glob("*.yml"):
        assert yaml.safe_load(path.read_text())["concurrency"]["cancel-in-progress"] is False
    job = yaml.safe_load((root / "ci/verification.job.yml").read_text())["resources"]["jobs"]["verification"]
    task = job["tasks"][0]
    assert task["task_key"] == "verify"
    assert task["spark_python_task"]["python_file"] == "../src/parity/parity_check.py"
    assert "bundle run -t prod verification" in (root / ".github/workflows/deploy-prod.yml").read_text()
    assert "bundle run -t dev" in (root / "ci/dev_verify.sh").read_text()
    for path in (root / "ci").glob("*"):
        if path.is_file():
            assert "jobs list" not in path.read_text()


@pytest.mark.parametrize("failed", [False, True])
def test_visible_task_exits_nonzero_on_verification_failure(monkeypatch, tmp_path, failed):
    import databricks.sdk
    import registry
    import runpy
    from pathlib import Path
    from test_manifest import client
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "golden/manifest.json").read_text())
    monkeypatch.setattr(registry, "load", lambda *_: {})
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda **_: client(manifest))
    monkeypatch.setattr(sys, "argv", ["parity_check", "--catalog", "invalid" if failed else "daiwt_01_mig_dev",
                                      "--output", str(tmp_path / "parity.json")])
    if failed:
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(root / "src/parity/parity_check.py"), run_name="__main__")
        assert exc.value.code == 1
    else:
        runpy.run_path(str(root / "src/parity/parity_check.py"), run_name="__main__")
        assert json.loads((tmp_path / "parity.json").read_text())["status"] == "PASS"


@pytest.mark.parametrize("jobs,ok", [({}, False), ({"build": {}}, False), ({"build": {"id": "42"}}, True)])
def test_job_resolution_uses_bundle_state(monkeypatch, jobs, ok):
    import run_units as runner
    from test_ci import mock_runs
    calls, summaries = [], []
    mock_runs(monkeypatch, calls)
    def summary(command, **kwargs):
        summaries.append(command)
        return json.dumps({"resources": {"jobs": jobs}})
    monkeypatch.setattr(subprocess, "check_output", summary)
    if ok:
        runner.run_units("pr_1", {"a": {"job": "build"}}, drop="D0")
        assert calls
    else:
        with pytest.raises(ValueError, match="not deployed in bundle state"):
            runner.run_units("pr_1", {"a": {"job": "build"}}, drop="D0")
        assert not calls
    assert summaries == [["databricks", "bundle", "summary", "-t", "dev", "--var",
                          "schema_suffix=pr_1", "--output", "json"]]
