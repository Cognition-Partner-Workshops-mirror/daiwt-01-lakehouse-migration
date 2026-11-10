import csv
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from parity.build_contract import build
from parity.parity_check import compare, manifest_drops, output_entries

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("capture_snapshot", ROOT / "golden/capture_snapshot.py")
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
COMMITTED = json.loads((ROOT / "golden/manifest.json").read_text())


@pytest.mark.parametrize("value,kind,expected", [
    (None, "VARCHAR(10)", "\\N"), (float("nan"), "DECIMAL(18,2)", "\\N"), ("", "VARCHAR(10)", ""),
    (5, "INTEGER", "5"), (20250630.0, "BIGINT", "20250630"),
    (1.5, "DECIMAL(18,2)", "1.50"), (11192775.979999999, "DECIMAL(18,2)", "11192775.98"),
    ("-0.00", "DECIMAL(18,2)", "0.00"), (-0.0, "DECIMAL(18,2)", "0.00"), (-0.1, "DECIMAL(18,2)", "-0.10"), ("2.345", "DECIMAL(9,4)", "2.3450"),
    ("2025-06-30", "DATE", "2025-06-30"),
    ("2025-06-30 23:00:00", "TIMESTAMP(0)", "2025-06-30T23:00:00.000Z"),
    ("NO", "CHAR(2)", "NO"), ("Bodø", "VARCHAR(40)", "Bodø"),
])
def test_render_matches_statements_api(value, kind, expected):
    assert capture.render(value, kind) == expected


@pytest.mark.parametrize("value,kind,error", [
    (1.5, "INTEGER", "non-integral"), (True, "INTEGER", "non-integral"),
    (1.234, "DECIMAL(18,2)", "would round"), ("30/06/2025", "DATE", "does not match"),
    (b"x", "BLOB", "unsupported"),
])
def test_render_fails_instead_of_fixing_values(value, kind, error):
    with pytest.raises(ValueError, match=error):
        capture.render(value, kind)


def test_ddl_columns_preserve_order_and_types():
    ddl = """CREATE MULTISET TABLE nvb_edw.t
(
    account_id INTEGER,
    dormant_flag CHAR(1) CHARACTER SET LATIN COMPRESS ('N','Y'),
    load_ts TIMESTAMP(0) NOT NULL DEFAULT TIMESTAMP '2025-06-30 23:00:00',
    overdraft_limit DECIMAL(18, 2) DEFAULT 0
)
PRIMARY INDEX (account_id);"""
    assert capture.ddl_columns(ddl) == [("account_id", "INTEGER"), ("dormant_flag", "CHAR(1)"),
                                        ("load_ts", "TIMESTAMP(0)"), ("overdraft_limit", "DECIMAL(18,2)")]


def test_write_output_sorts_quotes_and_rejects_duplicate_keys(tmp_path):
    columns = [("id", "INTEGER"), ("name", "VARCHAR(20)"), ("amount", "DECIMAL(18,2)")]
    path = tmp_path / "outputs/D0/unit/t.csv"
    rows = capture.write_output(path, columns, [
        {"id": 2, "name": "a,b", "amount": None}, {"id": 1, "name": "", "amount": 3.1}], ["id"])
    assert rows == 2
    assert path.read_bytes() == b'id,name,amount\n1,,3.10\n2,"a,b",\\N\n'
    with pytest.raises(ValueError, match="duplicate key"):
        capture.write_output(path, columns, [{"id": 1, "name": "x", "amount": 1}] * 2, ["id"])


def delivery(tmp_path, tables=capture.INPUTS):
    exports = tmp_path / "exports"
    entries = []
    for table in tables:
        folder = exports / table
        folder.mkdir(parents=True)
        (folder / f"{table}.csv").write_text("id|v\n1|a\n")
        (folder / f"{table}.parquet").write_bytes(b"PAR1")
        entries.append({"table": table, "rows": 1, "md5": hashlib.md5(b"id|v\n1|a\n").hexdigest()})
    (exports / "_manifest.json").write_text(json.dumps(entries))
    return exports, entries


def test_s3_cross_check(tmp_path):
    exports, entries = delivery(tmp_path)
    s3 = tmp_path / "s3.json"
    s3.write_text(json.dumps(entries))
    result = capture.s3_cross_check(exports / "_manifest.json", s3, exports)
    assert len(result) == 11 and all(v["matches_s3"] for v in result.values())
    entries[0]["md5"] = "0" * 32
    s3.write_text(json.dumps(entries))
    with pytest.raises(ValueError, match="differs from S3 manifest"):
        capture.s3_cross_check(exports / "_manifest.json", s3, exports)
    s3.write_text(json.dumps(entries[1:]))
    with pytest.raises(ValueError, match="table sets differ"):
        capture.s3_cross_check(exports / "_manifest.json", s3, exports)


def fake_legacy(tmp_path):
    legacy = tmp_path / "legacy"
    (legacy / "legacy/bteq").mkdir(parents=True)
    (legacy / "legacy/views").mkdir()
    (legacy / "legacy/schedules").mkdir()
    (legacy / "data").mkdir()
    (legacy / "golden").mkdir()
    (legacy / "legacy/bteq/01_customer.btq").write_text(
        "EXEC register_batch;\n.IF ERRORCODE <> 0 THEN .GOTO rejected\n.EXPORT REPORT FILE = control_reports/x.txt\n")
    (legacy / "legacy/views/v_x.sql").write_text("REPLACE VIEW v_x AS SELECT 1;")
    (legacy / "legacy/schedules/nightly.yaml").write_text(
        "jobs:\n  - id: upstream\n    type: delivery\n  - id: customer\n    script: legacy/bteq/01_customer.btq\n")
    (legacy / "data/generate.py").write_text("rng = np.random.default_rng(1704)\n")
    for name in ("data/column_defaults.json", "golden/compute_baselines.py", "requirements.txt", "Makefile"):
        (legacy / name).write_text("x\n")
    return legacy


def test_skipped_branch_inventory(tmp_path):
    branches = {(i["path"], i["branch"]) for i in capture.skipped_branches(fake_legacy(tmp_path))}
    assert ("legacy/bteq/01_customer.btq", "rejected/failed") in branches
    assert ("legacy/bteq/01_customer.btq", "export control_reports/x.txt") in branches
    assert ("legacy/views/v_x.sql", "view") in branches
    assert ("legacy/schedules/nightly.yaml", "job upstream") in branches
    assert ("legacy/schedules/nightly.yaml", "job customer") not in branches


def synthetic_capture(tmp_path):
    root, legacy = tmp_path / "capture", fake_legacy(tmp_path)
    exports, entries = delivery(tmp_path)
    for table in capture.INPUTS:
        dest = root / f"inputs/D0/{table}"
        dest.mkdir(parents=True)
        for f in (exports / table).iterdir():
            (dest / f.name).write_bytes(f.read_bytes())
    (root / "inputs/D0/_manifest.json").write_text(json.dumps(entries))
    (root / "controls/D0/expected").mkdir(parents=True)
    (root / "controls/D0/summary.csv").write_text("table,rows\n")
    outputs = {}
    for table, (_, unit, _, keys) in capture.OUTPUTS.items():
        columns = [(k, "INTEGER") for k in keys] + [("v", "VARCHAR(5)")]
        rows = capture.write_output(root / f"outputs/D0/{unit}/{table.split('.')[1]}.csv", columns,
                                    [{**{k: 1 for k in keys}, "v": None}], keys)
        outputs[table] = {"columns": columns, "rows": rows, "excluded_constants": {"load_ts": "2025-06-30 23:00:00"}}
    s3 = {t: {"rows": 1, "md5": e["md5"]} for t, e in zip(capture.INPUTS, entries)}
    s3_manifest = tmp_path / "s3.json"
    s3_manifest.write_text(json.dumps(entries))
    manifest = capture.build_manifest(root, legacy, "a" * 40, "2026-11-10T16:21:54Z", {}, s3,
                                      {"outputs": outputs}, s3_manifest)
    return root, manifest


def units_for(manifest):
    units = {}
    for entry in manifest["files"]:
        if entry["path"].startswith("outputs/"):
            units.setdefault(entry["unit"], {"outputs": []})["outputs"].append(
                {"table": entry["table"], "golden": entry["path"], "keys": entry["keys"]})
    return units


def test_manifest_matches_harness_layout(tmp_path):
    root, manifest = synthetic_capture(tmp_path)
    assert manifest["capture_id"] == "20261110T162154Z" and manifest["capture_mode"] == "snapshot"
    assert manifest["source"]["generator_seed"] == 1704
    assert manifest_drops(manifest) == ["D0"]
    outputs = output_entries(manifest, "D0")
    assert len(outputs) == 10 and all(e["columns"][-1] == "v" for e in outputs)
    contract = build(manifest, units_for(manifest))
    assert len(contract["outputs"]) == 10 and {o["kind"] for o in contract["outputs"]} == {"table"}

    def read_golden(entry):
        with open(root / entry["path"], encoding="utf-8") as stream:
            reader = csv.reader(stream)
            assert next(reader) == entry["columns"]
            return [[None if v == "\\N" else v for v in row] for row in reader]

    registry = {u: {"outputs": [o["table"] for o in units_for(manifest)[u]["outputs"]]}
                for u in units_for(manifest)}
    result = compare({"tables": outputs}, registry, read_golden, read_golden)
    assert result["status"] == "PASS" and len(result["results"]) == 10
    assert capture.verify(root, manifest) == len(manifest["files"])
    (root / outputs[0]["path"]).write_text("tampered\n")
    with pytest.raises(ValueError, match="checksum mismatches"):
        capture.verify(root, manifest)


def test_capture_never_overwrites(tmp_path):
    (tmp_path / "manifest.json").write_text("{}")
    with pytest.raises(FileExistsError):
        capture.capture(tmp_path, "repo", "a" * 40, "2026-11-10T16:21:54Z")


def test_committed_manifest_is_the_snapshot_capture():
    m = COMMITTED
    assert m["capture_mode"] == "snapshot"
    assert re.fullmatch(r"\d{8}T\d{6}Z", m["capture_id"])
    assert m["capture_id"] == capture.capture_id(m["captured_at_utc"])
    assert re.fullmatch(r"[0-9a-f]{40}", m["source"]["legacy_commit"])
    assert "not the result of a Teradata execution" in m["source"]["statement"]
    assert m["source"]["generator_seed"] == 1704
    assert manifest_drops(m) == ["D0"]
    inputs = [f for f in m["files"] if f["path"].startswith("inputs/") and f.get("table")]
    assert sorted({f["table"] for f in inputs}) == sorted(capture.INPUTS)
    assert sorted(f["format"] for f in inputs) == sorted(["csv", "parquet"] * 11)
    assert all(f["md5_matches_s3_manifest"] for f in inputs if f["format"] == "csv")
    outputs = output_entries(m, "D0")
    assert [e["path"].split("/")[2] for e in outputs] == [u for _, u, _, _ in capture.OUTPUTS.values()]
    assert all("load_ts" not in e["columns"] for e in outputs)
    assert [x["column"] for x in m["excluded_from_parity"]] == ["load_ts"]
    assert len(build(m, units_for(m))["outputs"]) == 10
    assert all(re.fullmatch(r"[0-9a-f]{64}", f["sha256"]) for f in m["files"])
    paths = [f["path"] for f in m["files"]]
    assert len(paths) == len(set(paths))


def test_committed_provenance_and_inventory_pin_the_manifest():
    provenance = json.loads((ROOT / "golden/PROVENANCE.json").read_text())
    inventory = json.loads((ROOT / "golden/skipped_branches.json").read_text())
    digest = hashlib.sha256((ROOT / "golden/manifest.json").read_bytes()).hexdigest()
    assert provenance["manifest_sha256"] == digest
    assert provenance["capture_id"] == inventory["capture_id"] == COMMITTED["capture_id"]
    assert provenance["staging_path"] == (
        f"/Volumes/daiwt_01_mig_prod/landing/golden/_staging/{COMMITTED['capture_id']}/")
    assert provenance["legacy_commit"] == COMMITTED["source"]["legacy_commit"]
    assert inventory["skipped_branches"] and all(i["reason"] for i in inventory["skipped_branches"])


def test_no_captured_data_is_committed():
    golden = ROOT / "golden"
    assert not [p for p in golden.rglob("*") if p.suffix in (".csv", ".parquet")]
