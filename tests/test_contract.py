import copy
import json
from pathlib import Path

import pytest
from databricks.sdk.errors import NotFound

from parity.build_contract import build
from parity.parity_check import GoldenStore, Warehouse, compare, contract_entries, run as run_parity
from test_manifest import client

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = {"drops": ["D0"], "files": [
    {"path": "outputs/D0/daily_revenue/revenue.csv", "table": "gold.daily_revenue",
     "sha256": "table-hash", "row_count": 1, "keys": ["id"], "columns": ["id"]},
    {"path": "outputs/D0/daily_revenue/report.csv",
     "sha256": "report-hash", "row_count": 1, "keys": ["id"], "columns": ["id"]},
]}
CONTRACT = {"outputs": [
    {**entry, "drop": "D0", "unit": "daily_revenue",
     "table": entry.get("table", "gold.report_daily_revenue"),
     "kind": "table" if "table" in entry else "report"}
    for entry in MANIFEST["files"]
]}


def run(*args, **kwargs):
    return run_parity(*args, contract=CONTRACT, **kwargs)


def test_unpromoted_capture_has_empty_contract():
    manifest = json.loads((ROOT / "golden/manifest.json").read_text())
    contract = json.loads((ROOT / "src/parity/contract.json").read_text())
    assert manifest["capture_mode"] == "snapshot"
    assert contract == {"outputs": []}
    result = run_parity(client(error=NotFound("staged, not promoted")), "daiwt_01_mig_dev", "pr_1", "w", {},
                        contract=contract)
    assert result["status"] == "PASS"
    assert result["goldens"] == result["compared"] == 0



def test_all_outputs_have_unique_targets():
    paths = [f["path"] for f in MANIFEST["files"] if f["path"].startswith("outputs/")]
    outputs = CONTRACT["outputs"]
    assert len(paths) == len(outputs) == len(set(paths)) == 2
    assert set(paths) == {e["path"] for e in outputs}
    assert len({e["table"] for e in outputs}) == 2
    assert sum(e["kind"] == "table" for e in outputs) == 1
    assert sum(e["kind"] == "report" for e in outputs) == 1
    for entry in outputs:
        assert set(entry) == {"path", "drop", "unit", "table", "kind", "keys",
                              "sha256", "row_count", "columns"}
        captured = next(f for f in MANIFEST["files"] if f["path"] == entry["path"])
        for key in ("sha256", "row_count", "columns", "keys"):
            assert entry[key] == captured[key]
        if entry["kind"] == "report":
            assert entry["table"] == "gold.report_" + entry["unit"]
        else:
            assert entry["table"] == captured["table"]


def test_empty_registry_skips_every_output():
    registry = json.loads((ROOT / "src/sql/units/registry.json").read_text())
    assert registry == {}
    result = run(client(MANIFEST), "daiwt_01_mig_dev", "pr_1", "w", registry)
    assert result["status"] == "PASS"
    assert result["counts"] == {"PASS": 0, "FAIL": 0, "SKIP": 2}
    assert {r["reason"] for r in result["results"]} == {"not yet migrated"}


@pytest.mark.parametrize("catalog", ["daiwt_01_mig_dev", "daiwt_01_mig_prod"])
def test_unknown_registered_unit_rejected(catalog):
    with pytest.raises(ValueError, match="registered units absent from parity contract: typo_unit"):
        run(client(MANIFEST), catalog, "pr_1", "w",
            {"typo_unit": {"outputs": []}}, ran_units=[])


def test_unknown_registered_unit_cli_fails(monkeypatch, tmp_path, capsys):
    import sys
    import databricks.sdk
    from parity import parity_check
    for directory, content in [("src/sql/units", {"typo_unit": {"outputs": []}}),
                               ("src/parity", CONTRACT)]:
        target = tmp_path / directory
        target.mkdir(parents=True)
        filename = "registry.json" if directory.endswith("units") else "contract.json"
        (target / filename).write_text(json.dumps(content))
    monkeypatch.setattr(parity_check, "ROOT", tmp_path)
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda **_: client(MANIFEST))
    output = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", ["parity_check", "--output", str(output)])
    assert parity_check.main() == 1
    result = json.loads(output.read_text())
    assert result["status"] == "FAIL"
    assert result["error"] == "registered units absent from parity contract: typo_unit"
    assert result["error"] in capsys.readouterr().out


@pytest.mark.parametrize("mutation", ["missing", "extra", "sha256", "duplicate"])
def test_volume_manifest_drift_fails_closed(mutation):
    manifest = copy.deepcopy(MANIFEST)
    entry = next(f for f in manifest["files"] if f["path"].startswith("outputs/"))
    if mutation == "missing":
        manifest["files"].remove(entry)
    elif mutation == "extra":
        manifest["files"].append({**entry, "path": "outputs/D0/new/new.csv"})
    elif mutation == "sha256":
        entry["sha256"] = "changed"
    else:
        manifest["files"].append(entry)
    with pytest.raises(ValueError, match="contract/manifest|duplicate"):
        run(client(manifest), "daiwt_01_mig_dev", "pr_1", "w", {})


def test_missing_manifest_fails_with_pinned_contract():
    with pytest.raises(ValueError, match="contract/manifest"):
        run(client(error=NotFound("missing")), "daiwt_01_mig_dev", "pr_1", "w", {})


@pytest.mark.parametrize("field", ["path", "table"])
def test_duplicate_contract_rejected(field):
    contract = copy.deepcopy(CONTRACT)
    contract["outputs"][1][field] = contract["outputs"][0][field]
    with pytest.raises(ValueError, match="duplicate contract"):
        contract_entries(contract, MANIFEST, "D0")


@pytest.mark.parametrize("catalog,schema", [
    ("daiwt_01_mig_dev", "gold_pr_1"), ("daiwt_01_mig_prod", "gold")])
def test_registered_unit_compares_table_and_report(monkeypatch, catalog, schema):
    queries = []
    monkeypatch.setattr(GoldenStore, "rows", lambda *_: [["value"]])
    monkeypatch.setattr(Warehouse, "sql", lambda _, query: queries.append(query) or [["value"]])
    registry = {"daily_revenue": {"outputs": ["gold.daily_revenue"]}}
    result = run(client(MANIFEST), catalog, "pr_1", "w", registry)
    assert result["status"] == "PASS"
    assert result["counts"] == {"PASS": 2, "FAIL": 0, "SKIP": 0}
    assert len(queries) == 2
    assert all(f"`{schema}`" in q for q in queries)
    assert "`report_daily_revenue`" in queries[1]


@pytest.mark.parametrize("catalog,compared,reason", [
    ("daiwt_01_mig_dev", 0, "not run in this verification"),
    ("daiwt_01_mig_prod", 2, "matched")])
def test_dev_scope_does_not_change_production(monkeypatch, catalog, compared, reason):
    monkeypatch.setattr(GoldenStore, "rows", lambda *_: [])
    monkeypatch.setattr(Warehouse, "sql", lambda *_: [])
    result = run(client(MANIFEST), catalog, "pr_1", "w",
                 {"daily_revenue": {"outputs": []}}, ran_units=[])
    assert result["status"] == "PASS"
    assert result["compared"] == compared
    daily = [r for r in result["results"] if r["table"] in
             ("gold.daily_revenue", "gold.report_daily_revenue")]
    assert {r.get("reason") for r in daily} == {reason}


def test_registered_read_failure_is_not_skipped():
    entries = contract_entries(CONTRACT, MANIFEST, "D0")
    def broken(_):
        raise RuntimeError("missing target")
    result = compare({"tables": entries}, {"daily_revenue": {"outputs": []}},
                     lambda _: [], broken)
    assert result["status"] == "FAIL"
    assert sum(r["status"] == "FAIL" for r in result["results"]) == 2


def test_generator_mapping_and_validation():
    entries = copy.deepcopy(CONTRACT["outputs"])
    units = {}
    for entry in entries:
        output = {"golden": f"golden/{entry['unit']}/{entry['path'].split('/')[-1]}",
                  "keys": entry["keys"]}
        output.update({"target_sql": "report.sql"} if entry["kind"] == "report"
                      else {"table": entry["table"]})
        units.setdefault(entry["unit"], {"outputs": []})["outputs"].append(output)
    assert build(MANIFEST, units) == CONTRACT
    assert build({"drops": ["D0"], "files": []}, {}) == {"outputs": []}
    units["daily_revenue"]["outputs"][0]["table"] = "gold.wrong"
    with pytest.raises(ValueError, match="target table mismatch"):
        build(MANIFEST, units)
