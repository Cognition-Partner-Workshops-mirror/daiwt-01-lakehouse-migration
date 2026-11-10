"""Snapshot capture of the DAIWT-01 golden inputs and outputs.

There is no live Teradata. The legacy repository's fixed-seed generator
(`data/generate.py`) produces the delivery and its Python reference controls
(`golden/compute_baselines.py`) materialize every derived table. This runner
reproduces both at a pinned legacy commit, cross-checks the delivery against
the S3 landing `_manifest.json`, and writes a checksummed capture directory:

    inputs/<drop>/<db.table>/<db.table>.{csv,parquet}  delivered files, byte-for-byte
    inputs/<drop>/_manifest.json                       delivery manifest
    outputs/<drop>/<unit>/<table>.csv                  derived tables (parity CSVs)
    controls/<drop>/expected/*.json, summary.csv       reference controls, unchanged
    manifest.json, PROVENANCE.json, skipped_branches.json

Top-level imports are stdlib only; pandas is imported in the `materialize`
phase, which runs inside the legacy repository's own virtual environment.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

LEGACY_REPO = "https://github.com/Cognition-Partner-Workshops-mirror/daiwt-01-teradata-warehouse.git"
S3_LANDING = "s3://daiwt-01-landing-599083837640/daiwt-01/landing/"
S3_REGION = "us-west-2"
DROP = {"name": "D0", "batch_id": 20250630, "business_date": "2025-06-30"}
NULL = "\\N"
EXCLUDED_COLUMNS = ("load_ts",)
INPUTS = (
    "nvb_edw.dim_account", "nvb_edw.dim_branch", "nvb_edw.dim_calendar", "nvb_edw.dim_card",
    "nvb_edw.dim_merchant", "nvb_edw.dim_product", "nvb_edw.fct_daily_balance",
    "nvb_edw.fct_loan", "nvb_edw.fct_product_order", "nvb_stg.customer_feed",
    "nvb_stg.transaction_feed",
)
# Derived table -> (legacy BTEQ job, unit, parity target, keys). Order follows the schedule.
OUTPUTS = {
    "nvb_edw.dim_customer": ("01_customer", "customer", "silver.dim_customer", ["customer_id"]),
    "nvb_edw.fct_transaction": ("02_transactions", "transactions", "silver.fct_transaction", ["transaction_id"]),
    "nvb_edw.dim_account": ("03_dormant", "dormant", "silver.dim_account", ["account_id"]),
    "nvb_mart.mart_monthly_balance": ("04_monthly_balance", "monthly_balance", "gold.mart_monthly_balance", ["account_id", "month_start"]),
    "nvb_mart.mart_customer_segment": ("05_customer_segment", "customer_segment", "gold.mart_customer_segment", ["customer_id"]),
    "nvb_mart.mart_branch_performance": ("06_branch_league", "branch_league", "gold.mart_branch_performance", ["branch_id"]),
    "nvb_mart.mart_product_profitability": ("07_product_profitability", "product_profitability", "gold.mart_product_profitability", ["product_id"]),
    "nvb_mart.mart_channel_daily": ("08_channel", "channel", "gold.mart_channel_daily", ["txn_date", "channel"]),
    "nvb_mart.mart_card_spend_category": ("09_card_category", "card_category", "gold.mart_card_spend_category", ["category"]),
    "nvb_edw.etl_batch_control": ("10_close_batch", "close_batch", "silver.etl_batch_control", ["batch_date", "job_name"]),
}
DDL_COLUMN = re.compile(r"^\s+([a-z_][a-z0-9_]*)\s+([A-Z]+(?:\(\d+(?:,\s*\d+)?\))?)")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def capture_id(captured_at):
    return datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y%m%dT%H%M%SZ")


def digest(path, algorithm="sha256"):
    h = hashlib.new(algorithm)
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n")


def ddl_columns(text):
    """Ordered (name, teradata_type) pairs from a CREATE TABLE statement."""
    body = text[text.index("\n(") + 2:text.index("\n)")]
    columns = []
    for line in body.splitlines():
        match = DDL_COLUMN.match(line)
        if match:
            columns.append((match.group(1), match.group(2).replace(" ", "")))
    if not columns:
        raise ValueError("no columns in DDL")
    return columns


def is_null(value):
    return value is None or (isinstance(value, float) and value != value)


def render(value, td_type):
    """Render one value as the Databricks SQL Statements API string for its target type."""
    if is_null(value):
        return NULL
    base = td_type.split("(")[0]
    if base in ("INTEGER", "BIGINT", "SMALLINT", "BYTEINT"):
        if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
            raise ValueError(f"non-integral {base} value: {value!r}")
        return str(int(value))
    if base == "DECIMAL":
        scale = int(td_type.rstrip(")").split(",")[1])
        exact = Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value))
        quantized = exact.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
        if isinstance(value, float) and abs(exact - quantized) > Decimal("1e-6"):
            raise ValueError(f"DECIMAL{td_type[7:]} would round {value!r}")
        return str(abs(quantized) if quantized == 0 else quantized)
    if base == "DATE":
        text = str(value)
        datetime.strptime(text, "%Y-%m-%d")
        return text
    if base == "TIMESTAMP":
        moment = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if base in ("CHAR", "VARCHAR"):
        return str(value)
    raise ValueError(f"unsupported Teradata type: {td_type}")


def write_output(path, columns, records, keys):
    """Write parity CSV; `records` are dicts of Python values, `columns` (name, type)."""
    rendered = sorted(([render(r[name], kind) for name, kind in columns] for r in records),
                      key=lambda row: [row[[c for c, _ in columns].index(k)] for k in keys] + row)
    key_idx = [[c for c, _ in columns].index(k) for k in keys]
    seen = set()
    for row in rendered:
        key = tuple(row[i] for i in key_idx)
        if key in seen:
            raise ValueError(f"duplicate key {key} in {path.name}")
        seen.add(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow([name for name, _ in columns])
        writer.writerows(rendered)
    return len(rendered)


def materialize(legacy, root):
    """Run inside the legacy venv: reference transform -> parity CSVs + evidence."""
    import importlib.util
    import pandas as pd
    spec = importlib.util.spec_from_file_location("compute_baselines", legacy / "golden/compute_baselines.py")
    controls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controls)
    exports = legacy / "data/exports"
    source = {p.parent.name: pd.read_parquet(p) for p in sorted(exports.glob("*/*.parquet"))}
    if sorted(source) != sorted(INPUTS):
        raise ValueError(f"unexpected delivery tables: {sorted(source)}")
    tables = controls.transform(source)
    evidence = {"input_parquet_rows": {k: len(v) for k, v in sorted(source.items())}, "outputs": {}}
    for table, (job, unit, target, keys) in OUTPUTS.items():
        frame = tables[table]
        expected = json.loads((legacy / f"golden/expected/{table}.json").read_text())
        recomputed = json.loads(json.dumps(controls.metrics(frame), sort_keys=True, allow_nan=False))
        stripped = {k: v for k, v in expected.items() if k not in ("table", "input_rows")}
        if recomputed != stripped:
            raise ValueError(f"{table}: materialized frame differs from committed control")
        ddl = ddl_columns((legacy / f"legacy/ddl/{table}.sql").read_text())
        frame_cols = [str(c) for c in frame.columns]
        if sorted(frame_cols) != sorted(name for name, _ in ddl):
            raise ValueError(f"{table}: reference columns {frame_cols} differ from DDL")
        excluded = {}
        for name in EXCLUDED_COLUMNS:
            values = sorted(set(frame[name].astype(str)))
            if len(values) != 1:
                raise ValueError(f"{table}.{name} is not constant: {values[:5]}")
            excluded[name] = values[0]
        columns = [(n, t) for n, t in ddl if n not in EXCLUDED_COLUMNS]
        data = {name: frame[name].tolist() for name, _ in columns}
        records = [dict(zip(data, row)) for row in zip(*data.values())]
        path = root / f"outputs/{DROP['name']}/{unit}/{table.split('.')[1]}.csv"
        rows = write_output(path, columns, records, keys)
        if rows != expected["rows"]:
            raise ValueError(f"{table}: {rows} rows vs control {expected['rows']}")
        evidence["outputs"][table] = {
            "columns": columns, "ddl_order_matches_reference": frame_cols == [n for n, _ in ddl],
            "excluded_constants": excluded, "rows": rows, "control_metrics_match": True}
    write_json(root / "run/materialize.json", evidence)


def run(cmd, cwd, log, env=None):
    with open(log, "a") as stream:
        stream.write(f"$ {' '.join(cmd)}\n")
        stream.flush()
        result = subprocess.run(cmd, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, env=env)
        stream.write(f"EXIT {result.returncode}\n")
    if result.returncode:
        raise RuntimeError(f"{' '.join(cmd)} failed; see {log}")


def tree_hashes(folder):
    return {str(p.relative_to(folder)): digest(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def git(legacy, *args):
    return subprocess.run(["git", *args], cwd=legacy, check=True, capture_output=True, text=True).stdout.strip()


def prepare_legacy(root, repo, commit):
    """Clone the pinned commit, `make setup`, then the local-only `make reset`."""
    legacy, runlog = root / "legacy", root / "run"
    runlog.mkdir(parents=True, exist_ok=True)
    if not legacy.exists():
        subprocess.run(["git", "clone", "-q", repo, str(legacy)], check=True)
    git(legacy, "checkout", "-q", "--detach", commit)
    if git(legacy, "rev-parse", "HEAD") != commit:
        raise ValueError("legacy checkout is not at the pinned commit")
    env = {k: v for k, v in os.environ.items() if k != "S3"}
    run(["make", "setup"], legacy, runlog / "make_setup.log", env)
    first = tree_hashes(legacy / "data/exports")
    clean_setup = git(legacy, "status", "--porcelain") == ""
    # Local reset only: `S3=` keeps the Makefile's upload branch disabled.
    run(["make", "reset", "S3="], legacy, runlog / "make_reset.log", env)
    second = tree_hashes(legacy / "data/exports")
    clean_reset = git(legacy, "status", "--porcelain") == ""
    if first != second:
        raise ValueError("delivery is not deterministic across make setup / make reset")
    if not (clean_setup and clean_reset):
        raise ValueError("make setup/reset changed tracked legacy files (committed controls drifted)")
    return {"setup": "make setup", "reset": "make reset S3= (local only)",
            "deterministic_delivery": True, "committed_controls_reproduced": True,
            "delivery_files": len(first)}


def s3_cross_check(local_manifest, s3_manifest, exports):
    local = {e["table"]: e for e in json.loads(Path(local_manifest).read_text())}
    remote = {e["table"]: e for e in json.loads(Path(s3_manifest).read_text())}
    if sorted(local) != sorted(remote) or sorted(local) != sorted(INPUTS):
        raise ValueError("delivery table sets differ from S3 landing manifest")
    result = {}
    for table in INPUTS:
        actual = digest(Path(exports) / table / f"{table}.csv", "md5")
        if not (actual == local[table]["md5"] == remote[table]["md5"]):
            raise ValueError(f"{table}: CSV md5 {actual} differs from S3 manifest {remote[table]['md5']}")
        if local[table]["rows"] != remote[table]["rows"]:
            raise ValueError(f"{table}: row count differs from S3 manifest")
        result[table] = {"md5": actual, "rows": remote[table]["rows"], "matches_s3": True}
    return result


def skipped_branches(legacy):
    """Inventory legacy paths the reference controls cannot exercise."""
    items = []
    for path in sorted((legacy / "legacy/bteq").glob("*.btq")):
        text = path.read_text()
        rel = str(path.relative_to(legacy))
        if ".GOTO rejected" in text:
            items.append({"path": rel, "branch": "rejected/failed",
                          "reason": "control-failure path (status FAILED, .QUIT 12, delivery held); "
                                    "the retained delivery passes every control, so only the success path is captured"})
        if "register_batch" in text:
            items.append({"path": rel, "branch": "register_batch",
                          "reason": "first-job OPEN/RUNNING batch-row transitions; only the final etl_batch_control state is materialized"})
        for report in re.findall(r"\.EXPORT REPORT FILE = (\S+)", text):
            items.append({"path": rel, "branch": f"export {report}",
                          "reason": "BTEQ report file written at run time; no Teradata execution, not materialized by reference controls"})
    for path in sorted((legacy / "legacy/views").glob("*.sql")):
        items.append({"path": str(path.relative_to(legacy)), "branch": "view",
                      "reason": "consumer view; not materialized by golden/compute_baselines.py"})
    schedule = (legacy / "legacy/schedules/nightly.yaml").read_text()
    for block in schedule.split("  - id: ")[1:]:
        if "script:" not in block:
            items.append({"path": "legacy/schedules/nightly.yaml", "branch": f"job {block.split()[0]}",
                          "reason": "scheduler step without a script (operator action); not executable locally"})
    items.append({"path": "legacy/schedules/nightly.yaml", "branch": "on_failure / restart",
                  "reason": "hold_descendants_and_page_owner and per-job restart scopes are scheduler behaviour; not exercised"})
    items.append({"path": "golden/compute_baselines.py", "branch": "ValueError rejections",
                  "reason": "duplicate/unmatched/invalid-delivery rejections raise; the retained delivery triggers none"})
    items.append({"path": "README.md", "branch": "later deliveries",
                  "reason": "only batch 20250630 exists; business-date literals are fixed, so one drop (D0)"})
    return items


def file_entry(root, path, **extra):
    return {"path": str(path.relative_to(root)), "sha256": digest(path), "bytes": path.stat().st_size, **extra}


def build_manifest(root, legacy, commit, captured_at, legacy_run, s3, evidence, s3_manifest, repo=LEGACY_REPO):
    drop = DROP["name"]
    files = []
    for table in INPUTS:
        folder = root / f"inputs/{drop}/{table}"
        header = (folder / f"{table}.csv").open(encoding="utf-8").readline().rstrip("\n").split("|")
        for ext in ("csv", "parquet"):
            files.append(file_entry(root, folder / f"{table}.{ext}", table=table, format=ext,
                                    row_count=s3[table]["rows"], columns=header,
                                    **({"md5": s3[table]["md5"], "md5_matches_s3_manifest": True}
                                       if ext == "csv" else {})))
    files.append(file_entry(root, root / f"inputs/{drop}/_manifest.json", format="json"))
    for table, (job, unit, target, keys) in OUTPUTS.items():
        out = evidence["outputs"][table]
        files.append(file_entry(
            root, root / f"outputs/{drop}/{unit}/{table.split('.')[1]}.csv",
            drop=drop, unit=unit, table=target, source_table=table, legacy_job=job,
            row_count=out["rows"], keys=keys,
            columns=[{"name": n, "source_type": t} for n, t in out["columns"]]))
    for path in sorted((root / f"controls/{drop}").rglob("*")):
        if path.is_file():
            files.append(file_entry(root, path, format=path.suffix[1:]))
    source = (legacy / "data/generate.py").read_text()
    excluded = {}
    for table, out in evidence["outputs"].items():
        for column, value in out["excluded_constants"].items():
            excluded.setdefault(column, {"value": value, "tables": []})["tables"].append(table)
    return {
        "capture_id": capture_id(captured_at),
        "captured_at_utc": captured_at,
        "capture_mode": "snapshot",
        "source": {
            "engine": "Teradata 17.x (mocked; no live Teradata instance exists)",
            "statement": "Outputs come from the legacy repository's Python reference controls "
                         "(golden/compute_baselines.py transform()), which the legacy README states implement "
                         "the same joins, windows and calculations as the nightly BTEQ. "
                         "They are not the result of a Teradata execution.",
            "legacy_repo": repo.removesuffix(".git"),
            "legacy_commit": commit,
            "legacy_run": legacy_run,
            "generator": "data/generate.py",
            "generator_seed": int(re.search(r"default_rng\((\d+)\)", source).group(1)),
            "source_sha256": {p: digest(legacy / p) for p in (
                "data/generate.py", "data/column_defaults.json", "golden/compute_baselines.py",
                "requirements.txt", "Makefile")},
            "s3_landing": S3_LANDING,
            "s3_landing_manifest_md5": digest(s3_manifest, "md5"),
            "s3_landing_manifest_sha256": digest(s3_manifest),
            "s3_cross_check": "every delivered CSV md5 and row count equals the S3 _manifest.json",
        },
        "drops": [DROP],
        "drops_reason": "single retained delivery (batch 20250630, business date 2025-06-30); full replacement load",
        "csv_conventions": {
            "inputs": {"files": "copied byte-for-byte as delivered", "delimiter": "|", "header": True,
                       "encoding": "UTF-8", "line_endings": "LF", "parquet": "snappy, alternate encoding of the same delivery"},
            "outputs": {"delimiter": ",", "quoting": "RFC4180 minimal", "header": "ordered manifest columns",
                        "encoding": "UTF-8", "line_endings": "LF", "null": NULL,
                        "integer": "decimal digits", "decimal": "fixed DDL scale, ROUND_HALF_UP; capture fails if a float would round by more than 1e-6",
                        "date": "YYYY-MM-DD", "timestamp": "TIMESTAMP(0) as Statements API TIMESTAMP in UTC: YYYY-MM-DDTHH:MM:SS.000Z",
                        "char": "value as delivered (no Teradata CHAR padding)",
                        "ordering": "sorted by keys; keys verified unique",
                        "column_order": "legacy DDL order minus excluded columns"},
        },
        "excluded_from_parity": [{"column": c, "constant_value": v["value"], "tables": v["tables"],
                                  "reason": "logical delivery timestamp, constant across every row; not a computed value"}
                                 for c, v in excluded.items()],
        "controls": f"controls/{drop}/expected/*.json and controls/{drop}/summary.csv are the legacy reference "
                    "baselines, unchanged; each output's metrics were recomputed and equal its control",
        "skipped_branches": "skipped_branches.json",
        "files": files,
    }


def verify(root, manifest):
    bad = [f["path"] for f in manifest["files"] if digest(root / f["path"]) != f["sha256"]]
    if bad:
        raise ValueError(f"checksum mismatches: {bad}")
    provenance, inventory = root / "PROVENANCE.json", root / "skipped_branches.json"
    if provenance.exists():
        pinned = json.loads(provenance.read_text())
        if pinned.get("manifest_sha256") != digest(root / "manifest.json"):
            raise ValueError("PROVENANCE.json does not pin this manifest")
        if pinned.get("capture_id") != manifest["capture_id"]:
            raise ValueError("PROVENANCE.json capture_id differs from manifest")
    if inventory.exists() and json.loads(inventory.read_text()).get("capture_id") != manifest["capture_id"]:
        raise ValueError("skipped_branches.json capture_id differs from manifest")
    return len(manifest["files"])


def capture(root, repo, commit, captured_at, s3_manifest=None):
    root = Path(root).resolve()
    existing = [n for n in ("manifest.json", "inputs", "outputs", "controls") if (root / n).exists()]
    if existing:
        raise FileExistsError(f"{root} already holds {existing}; captures are never overwritten")
    legacy = root / "legacy"
    legacy_run = prepare_legacy(root, repo, commit)
    if s3_manifest is None:
        s3_manifest = root / "run/s3_landing_manifest.json"
        subprocess.run(["aws", "s3", "cp", S3_LANDING + "_manifest.json", str(s3_manifest),
                        "--region", S3_REGION, "--only-show-errors"], check=True)
    exports = legacy / "data/exports"
    s3 = s3_cross_check(exports / "_manifest.json", s3_manifest, exports)
    drop = DROP["name"]
    for table in INPUTS:
        shutil.copytree(exports / table, root / f"inputs/{drop}/{table}", dirs_exist_ok=True)
    shutil.copy2(exports / "_manifest.json", root / f"inputs/{drop}/_manifest.json")
    shutil.copytree(legacy / "golden/expected", root / f"controls/{drop}/expected", dirs_exist_ok=True)
    shutil.copy2(legacy / "golden/summary.csv", root / f"controls/{drop}/summary.csv")
    subprocess.run([str(legacy / ".venv/bin/python"), __file__, "materialize",
                    "--legacy", str(legacy), "--capture-dir", str(root)], check=True)
    evidence = json.loads((root / "run/materialize.json").read_text())
    for table in INPUTS:
        if evidence["input_parquet_rows"][table] != s3[table]["rows"]:
            raise ValueError(f"{table}: Parquet rows differ from delivery manifest")
    manifest = build_manifest(root, legacy, commit, captured_at, legacy_run, s3, evidence, s3_manifest, repo)
    versions = subprocess.run([str(legacy / ".venv/bin/python"), "-c",
                               "import sys,pandas,numpy,pyarrow;print(sys.version.split()[0],pandas.__version__,numpy.__version__,pyarrow.__version__)"],
                              check=True, capture_output=True, text=True).stdout.split()
    inventory = {"capture_id": manifest["capture_id"], "skipped_branches": skipped_branches(legacy)}
    write_json(root / "skipped_branches.json", inventory)
    write_json(root / "manifest.json", manifest)
    checked = verify(root, manifest)
    provenance = {
        "capture_id": manifest["capture_id"],
        "staging_path": f"/Volumes/daiwt_01_mig_prod/landing/golden/_staging/{manifest['capture_id']}/",
        "manifest_sha256": digest(root / "manifest.json"),
        "legacy_commit": commit,
        "runtime": dict(zip(("python", "pandas", "numpy", "pyarrow"), versions)),
        "legacy_run": legacy_run,
        "s3_cross_check": s3,
        "outputs": {t: {k: v for k, v in e.items() if k != "columns"} for t, e in evidence["outputs"].items()},
        "local_verification": f"{checked} files, 0 checksum mismatches",
    }
    write_json(root / "PROVENANCE.json", provenance)
    return manifest, provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", nargs="?", default="capture", choices=["capture", "materialize", "verify"])
    parser.add_argument("--capture-dir", required=True, type=Path)
    parser.add_argument("--legacy-repo", default=LEGACY_REPO)
    parser.add_argument("--commit")
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--captured-at", default=utcnow())
    parser.add_argument("--s3-manifest", type=Path, help="local copy of the S3 landing _manifest.json")
    args = parser.parse_args(argv)
    if args.phase == "materialize":
        materialize(args.legacy, args.capture_dir)
        return 0
    if args.phase == "verify":
        manifest = json.loads((args.capture_dir / "manifest.json").read_text())
        print(f"{verify(args.capture_dir, manifest)} files, 0 checksum mismatches")
        return 0
    if not args.commit:
        parser.error("--commit is required for capture")
    manifest, provenance = capture(args.capture_dir, args.legacy_repo, args.commit, args.captured_at, args.s3_manifest)
    for f in manifest["files"]:
        if f["path"].startswith("outputs/") or f.get("format") == "csv" and f["path"].startswith("inputs/"):
            print(f"{f['path']:70} {f['row_count']:>9} rows")
    print(f"captured {DROP['name']} capture_id={manifest['capture_id']} manifest_sha256={provenance['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
