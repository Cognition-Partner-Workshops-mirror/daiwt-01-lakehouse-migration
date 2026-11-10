import copy
import json
from pathlib import Path

import pytest
from databricks.sdk.errors import NotFound

from parity.build_contract import build
from parity.parity_check import GoldenStore, Warehouse, compare, contract_entries, run
from test_manifest import client

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "src/parity/contract.json").read_text())
MANIFEST = json.loads((ROOT / "golden/manifest.json").read_text())


def test_all_outputs_have_unique_targets():
    paths = [f["path"] for f in MANIFEST["files"] if f["path"].startswith("outputs/")]
    outputs = CONTRACT["outputs"]
    assert len(paths) == len(outputs) == len(set(paths)) == len(CONTRACT["outputs"])
    assert set(paths) == {e["path"] for e in outputs}
    assert len({e["table"] for e in outputs}) == len(outputs)
    assert sum(e["kind"] == "table" for e in outputs) == 10
    assert sum(e["kind"] == "report" for e in outputs) == 0
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
    registry = {}
    result = run(client(MANIFEST), "daiwt_01_mig_dev", "pr_1", "w", registry)
    assert result["status"] == "PASS"
    assert result["counts"] == {"PASS": 0, "FAIL": 0, "SKIP": len(CONTRACT["outputs"])}
    assert {r["reason"] for r in result["results"]} == {"not yet migrated"}


@pytest.mark.parametrize("catalog", ["daiwt_01_mig_dev", "daiwt_01_mig_prod"])
def test_unknown_registered_unit_rejected(catalog):
    with pytest.raises(ValueError, match="legacy_unit not in contract"):
        run(client(MANIFEST), catalog, "pr_1", "w",
            {"typo_unit": {"job": "typo", "legacy_unit": "typo_unit", "outputs": [], "owns": [], "depends_on": []}}, ran_units=[])


def test_unknown_registered_unit_cli_fails(monkeypatch, tmp_path, capsys):
    import sys
    import databricks.sdk
    from parity import parity_check
    for directory, content in [("src/sql/units/typo_unit", {"job": "typo", "legacy_unit": "typo_unit", "outputs": [], "owns": [], "depends_on": []}),
                               ("src/parity", CONTRACT)]:
        target = tmp_path / directory
        target.mkdir(parents=True)
        filename = "unit.json" if directory.endswith("typo_unit") else "contract.json"
        (target / filename).write_text(json.dumps(content))
    monkeypatch.setattr(parity_check, "ROOT", tmp_path)
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda **_: client(MANIFEST))
    output = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", ["parity_check", "--output", str(output)])
    assert parity_check.main() == 1
    result = json.loads(output.read_text())
    assert result["status"] == "FAIL"
    assert result["error"] == "typo_unit: legacy_unit not in contract"
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
def test_registered_unit_compares_table(monkeypatch, catalog, schema):
    queries = []
    monkeypatch.setattr(GoldenStore, "rows", lambda *_: [["value"]])
    monkeypatch.setattr(Warehouse, "sql", lambda _, query: queries.append(query) or [["value"]])
    registry = {"channel": channel_spec()}
    result = run(client(MANIFEST), catalog, "pr_1", "w", registry)
    assert result["status"] == "PASS"
    assert result["counts"] == {"PASS": 1, "FAIL": 0, "SKIP": len(CONTRACT["outputs"]) - 1}
    assert len(queries) == 1
    assert all(f"`{schema}`" in q for q in queries)
    assert "`mart_channel_daily`" in queries[0]


@pytest.mark.parametrize("catalog,compared,reason", [
    ("daiwt_01_mig_dev", 0, "not selected"),
    ("daiwt_01_mig_prod", 1, "matched")])
def test_dev_scope_does_not_change_production(monkeypatch, catalog, compared, reason):
    monkeypatch.setattr(GoldenStore, "rows", lambda *_: [])
    monkeypatch.setattr(Warehouse, "sql", lambda *_: [])
    result = run(client(MANIFEST), catalog, "pr_1", "w",
                 {"channel": channel_spec()}, ran_units=[])
    assert result["status"] == "PASS"
    assert result["compared"] == compared
    daily = [r for r in result["results"] if r["table"] in
             ("gold.mart_channel_daily",)]
    assert {r.get("reason") for r in daily} == {reason}


def test_registered_read_failure_is_not_skipped():
    entries = contract_entries(CONTRACT, MANIFEST, "D0")
    def broken(_):
        raise RuntimeError("missing target")
    result = compare({"tables": entries}, {"channel": channel_spec()},
                     lambda _: [], broken)
    assert result["status"] == "FAIL"
    assert sum(r["status"] == "FAIL" for r in result["results"]) == 1


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
    units["customer"]["outputs"][0]["table"] = "gold.wrong"
    with pytest.raises(ValueError, match="target table mismatch"):
        build(MANIFEST, units)


def channel_spec():
    return dict(job='channel', legacy_unit='channel',
                outputs=['gold.mart_channel_daily'], owns=[], depends_on=[])
