import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from attest import render
from classify_changes import classify, expand
from registry import load
from drops import manifest_drops
from cleanup import candidates
from run_units import run_units

ROOT = Path(__file__).resolve().parents[1]


def mock_runs(monkeypatch, calls, code=0):
    class Process:
        stdout = ["Run URL: https://example/run/1\n", "completed\n"]
        returncode = code

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self):
            return self.returncode
    def popen(command, **kwargs):
        calls.append(command)
        return Process()
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: json.dumps({
        "resources": {"jobs": {key: {"id": str(i)} for i, key in enumerate(
            ["a", "b", "sale_job", "customer_job"], 1)}}}))


def test_dev_runs_all_registered_units(monkeypatch):
    calls = []
    mock_runs(monkeypatch, calls)
    urls = run_units("pr_1", {"sale": {"job": "sale_job"}, "customer": {"job": "customer_job"}}, drop="D1")
    assert [command[-1] for command in calls] == ["customer_job", "sale_job"]
    assert all(command[4:6] == ["dev", "--var"] for command in calls)
    script = (ROOT / "ci/dev_verify.sh").read_text()
    assert 'out/selected-units.json --drop "$D" --run-urls out/run-urls.txt' in script
    assert urls == ["Run URL: https://example/run/1"] * 2
    assert all(command[7:9] == ["--params", "drop=D1"] for command in calls)


@pytest.mark.parametrize("target,suffix", [("dev", "pr_1"), ("prod", "prod")])
def test_run_units_empty(monkeypatch, target, suffix):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected job"))
    assert run_units(suffix, {}, target=target, drop="D0") == []


def test_prod_run_units_helper(monkeypatch):
    calls = []
    mock_runs(monkeypatch, calls)
    run_units("prod", {"sale": {"job": "sale_job"}, "customer": {"job": "customer_job"}}, target="prod", drop="D2")
    assert [command[-1] for command in calls] == ["customer_job", "sale_job"]
    assert all(command[4:7] == ["prod", "--var", "schema_suffix=prod"] for command in calls)


def test_prod_replays_nightly_before_parity_and_attestation():
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-prod.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    replay = next(step for step in steps if step.get("name") == "Replay production drops")
    assert 'mapfile -t drops < <(python ci/drops.py)' in replay["run"]
    assert 'for drop in "${drops[@]}"; do' in replay["run"]
    assert 'databricks bundle run -t prod nvb_nightly --params "drop=$drop"' in replay["run"]
    assert "run_units.py" not in replay["run"]
    assert workflow["concurrency"]["cancel-in-progress"] is False
    commands = "\n".join(step.get("run", "") for step in steps)
    positions = [commands.index("databricks bundle deploy -t prod"),
                 commands.index('databricks bundle run -t prod nvb_nightly --params "drop=$drop"'),
                 commands.index('databricks bundle run -t prod verification --params "drop=$drop"'),
                 commands.index('python src/parity/parity_check.py --catalog daiwt_01_mig_prod --suffix prod --drop "$drop"'),
                 commands.index('python ci/attest.py "${files[@]}"')]
    assert positions == sorted(positions)


@pytest.mark.parametrize("target,suffix", [("dev", "prod"), ("prod", "pr_1"), ("other", "pr_1")])
def test_run_units_rejects_invalid_target_suffix(target, suffix):
    with pytest.raises(ValueError):
        run_units(suffix, {}, target=target)


def test_run_units_failure(monkeypatch):
    mock_runs(monkeypatch, [], code=1)
    with pytest.raises(subprocess.CalledProcessError):
        run_units("pr_1", {"sale": {"job": "sale_job"}}, drop="D0")


@pytest.mark.parametrize("status,ok", [("PASS", True), ("SKIP", True), ("FAIL", False), ("UNKNOWN", False)])
def test_attestation(status, ok):
    result = parity_result()
    result["results"][0].update(status=status, reason="detail")
    result["counts"] = {s: sum(r["status"] == s for r in result["results"])
                        for s in ("PASS", "FAIL", "SKIP")}
    result["compared"] = result["counts"]["PASS"] + result["counts"]["FAIL"]
    text, valid = render(result)
    assert valid is ok
    assert f"| gold.table_0 | D2 | {status} | detail |" in text


def test_attest_empty():
    text, valid = render(parity_result())
    assert valid
    assert "0 outputs compared — not parity evidence" in text
    assert "compared 0/18" in text


def parity_result(drop="D2"):
    return {"status": "PASS", "drop": drop, "goldens": 18,
            "counts": {"PASS": 0, "FAIL": 0, "SKIP": 18}, "compared": 0,
            "results": [{"table": f"gold.table_{i}", "drop": drop,
                         "status": "SKIP", "reason": "not yet migrated"} for i in range(18)]}


@pytest.mark.parametrize("result", [
    {**parity_result(), "status": "FAIL", "error": "denied"},
    {**parity_result(), "goldens": 1},
    {**parity_result(), "compared": 1},
    {**parity_result(), "counts": {"PASS": 1, "FAIL": 0, "SKIP": 17}},
    {**parity_result(), "results": [parity_result()["results"][0]] * 18},
])
def test_attest_inconsistent(result):
    assert not render(result)[1]


def test_attest_missing_and_invalid(tmp_path):
    result = tmp_path / "missing.json"
    output = tmp_path / "attestation.md"
    command = [sys.executable, str(ROOT / "ci/attest.py"), str(result), "--output", str(output)]
    assert subprocess.run(command).returncode == 1
    assert "**FAIL**" in output.read_text()
    result.write_text("not json")
    assert subprocess.run(command).returncode == 1


def test_attest_multiple_files(tmp_path):
    paths = []
    for drop in ("D0", "D1", "D2"):
        path = tmp_path / f"{drop}.json"
        path.write_text(json.dumps(parity_result(drop)))
        paths.append(str(path))
    output = tmp_path / "attestation.md"
    command = [sys.executable, str(ROOT / "ci/attest.py"), *paths, "--output", str(output)]
    assert subprocess.run(command).returncode == 0
    assert all(f"PASS {drop}" in output.read_text() for drop in ("D0", "D1", "D2"))
    Path(paths[1]).write_text("{}")
    assert subprocess.run(command).returncode == 1
    assert "**FAIL**" in output.read_text()


def test_classification():
    registry = {"load_sale": {}, "load_customer": {}}
    assert classify({}, ["src/parity/parity_check.py"])['required']
    assert classify(registry, ["src/sql/units/load_sale/query.sql"])['units'] == ["load_sale"]
    assert classify(registry, ["src/parity/parity_check.py"])['units'] == ["load_customer", "load_sale"]
    assert not classify(registry, ["README.md"])['required']


def test_cleanup_scoping():
    schemas = [SimpleNamespace(name=n, comment=c) for n, c in [
        ("gold_pr_1", "daiwt-01-migration"), ("bronze_pr_2", "daiwt-01-migration"),
        ("landing", "daiwt-01-migration"), ("gold_pr_3", "another-repo"),
        ("gold", "daiwt-01-migration"), ("gold_pr_1_extra", "daiwt-01-migration"),
        ("pr_4_silver_pr_4", "daiwt-01-migration"), ("pr_1_gold_pr_4", "daiwt-01-migration")]]
    assert list(candidates(schemas, {1})) == ["gold_pr_1"]
    assert list(candidates(schemas, None)) == ["gold_pr_1", "bronze_pr_2", "pr_4_silver_pr_4"]


def test_registry_and_bundle_targets():
    registry = load(ROOT)
    assert all(set(spec) >= {"job", "outputs"} for spec in registry.values())
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text())
    assert "resources" not in bundle["targets"]["prod"]
    assert set(bundle["targets"]["dev"]["resources"]["schemas"]) == {"bronze", "silver", "gold"}
    assert "name_prefix" not in bundle["targets"]["dev"]["presets"]
    assert bundle["targets"]["prod"]["permissions"] == [
        {"service_principal_name": "${workspace.current_user.userName}", "level": "CAN_MANAGE"},
        {"group_name": "users", "level": "CAN_VIEW"}]


def test_workflow_oidc_environments():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = yaml.safe_load(path.read_text())
        assert workflow["permissions"]["id-token"] == "write"
        for name, job in workflow["jobs"].items():
            if name == "unit-tests":
                continue
            assert job["environment"] in ("dev", "prod", "${{ inputs.target }}")
            assert job["env"]["DATABRICKS_AUTH_TYPE"] == "github-oidc"


@pytest.mark.parametrize("path,expected", [
    ("src/sql/units/a/query.sql", ["a"]),
    ("src/sql/units/c/unit.json", ["a", "b", "c"]),
    ("resources/a.job.yml", ["a", "b", "c", "z"]),
    ("ci/drops.py", ["a", "b", "c", "z"]),
    ("src/sql/units/removed/unit.json", []),
    ("docs/readme.md", []), ("tests/test_ci.py", []), ("README.md", []), (".github/workflows/pr.yml", [])])
def test_selection(path, expected):
    registry = {"c": {"depends_on": ["b"]}, "a": {}, "b": {"depends_on": ["a"]}, "z": {}}
    assert classify(registry, [path])['units'] == expected


def test_expand():
    registry = {"b": {"depends_on": ["a"]}, "a": {}, "c": {}}
    assert expand(registry, ["a"]) == ["a"]
    assert expand(registry, "*") == ["a", "b", "c"]
    assert expand(registry, []) == []
    with pytest.raises(ValueError):
        expand(registry, ["unknown"])


def test_pr_only_reads_evidence():
    workflow = (ROOT / ".github/workflows/pr.yml").read_text()
    assert all(command not in workflow for command in ("bundle deploy", "run_units", "dev_verify"))


def test_run_urls_survive_failure(monkeypatch, tmp_path):
    mock_runs(monkeypatch, [], code=1)
    path = tmp_path / "urls.txt"
    with pytest.raises(subprocess.CalledProcessError):
        run_units("pr_1", {"a": {"job": "a"}}, drop="D0", run_urls=path)
    assert path.read_text() == "Run URL: https://example/run/1\n"


def test_cleanup_single_suffix():
    schemas = [SimpleNamespace(name=f"gold_pr_{n}", comment="daiwt-01-migration") for n in (1, 2)]
    assert list(candidates(schemas, None, "pr_2")) == ["gold_pr_2"]
