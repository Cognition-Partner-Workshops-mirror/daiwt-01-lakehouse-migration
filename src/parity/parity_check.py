"""Compare registered SQL unit outputs with published CSV goldens."""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

GOLDEN_PATH = "/Volumes/daiwt_01_mig_prod/landing/golden"
ROOT = Path(globals().get("__file__", sys.argv[0])).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ci'))
from registry import load, validate
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def identifier(value):
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid identifier: {value}")
    return f"`{value}`"


def digest(rows):
    encoded = sorted(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
    return hashlib.sha256(json.dumps(encoded, separators=(",", ":")).encode()).hexdigest()


def manifest_drops(manifest):
    drops = manifest.get("drops", ["D0"])
    if not isinstance(drops, list) or not drops:
        raise ValueError("manifest.drops must be a non-empty list")
    names = [entry.get("name") if isinstance(entry, dict) else entry for entry in drops]
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in names):
        raise ValueError("invalid manifest drop name")
    if len(names) != len(set(names)):
        raise ValueError("duplicate manifest drop")
    return names


def output_entries(manifest, drop):
    drops = manifest_drops(manifest)
    if drop not in drops:
        raise ValueError("unknown drop")
    entries = manifest["files"] if "files" in manifest else manifest["tables"]
    if not isinstance(entries, list):
        raise ValueError("manifest files must be a list")
    outputs = []
    for entry in entries:
        path = entry["path"]
        if not path.startswith("outputs/"):
            continue
        parts = path.split("/")
        if ".." in parts:
            raise ValueError("Golden path must be under outputs/")
        path_drop = parts[1] if len(parts) > 2 else None
        for label in (path_drop, entry.get("drop")):
            if label is not None and label not in drops:
                raise ValueError(f"undeclared golden output drop: {label}")
        if path_drop is not None and entry.get("drop", path_drop) != path_drop:
            raise ValueError(f"golden output drop mismatch: {path_drop} vs {entry['drop']}")
        if entry.get("drop", path_drop or drop) != drop:
            continue
        columns = [column["name"] if isinstance(column, dict) else column for column in entry["columns"]]
        outputs.append({**entry, "columns": columns})
    return outputs


def contract_entries(contract, manifest, drop):
    captured = [entry for name in manifest_drops(manifest) for entry in output_entries(manifest, name)]
    outputs = contract["outputs"]
    paths = [entry["path"] for entry in outputs]
    targets = [entry["table"] for entry in outputs]
    if len(paths) != len(set(paths)) or len(targets) != len(set(targets)):
        raise ValueError("duplicate contract output path or target table")
    remote = {entry["path"]: entry for entry in captured}
    if len(remote) != len(captured):
        raise ValueError("duplicate manifest output path")
    if set(paths) != set(remote):
        raise ValueError(f"contract/manifest output paths disagree: "
                         f"missing={sorted(set(paths) - set(remote))}, extra={sorted(set(remote) - set(paths))}")
    for entry in outputs:
        if entry["drop"] not in manifest_drops(manifest):
            raise ValueError(f"undeclared contract drop: {entry['drop']}")
        if entry["path"].split("/") != ["outputs", entry["drop"], entry["unit"],
                                        entry["path"].split("/")[-1]]:
            raise ValueError(f"contract unit/drop differs from path: {entry['path']}")
        if not re.fullmatch(r"(silver|gold)\.[a-z][a-z0-9_]*", entry["table"]):
            raise ValueError(f"invalid contract target: {entry['table']}")
        if entry["sha256"] != remote[entry["path"]]["sha256"]:
            raise ValueError(f"contract/manifest sha256 mismatch: {entry['path']}")
    if drop not in manifest_drops(manifest):
        raise ValueError("unknown drop")
    return [{**entry, "columns": [c["name"] if isinstance(c, dict) else c for c in entry["columns"]]}
            for entry in outputs if entry["drop"] == drop]


def compare(manifest, registry, read_golden, read_actual, ran_units=None):
    tables = manifest["tables"]
    if not isinstance(tables, list):
        raise ValueError("manifest.tables must be a list")
    producers = {}
    for unit, spec in registry.items():
        for table in spec["outputs"]:
            if table in producers:
                raise ValueError(f"Multiple producers for {table}")
            producers[table] = unit
    results = []
    seen = set()
    for entry in tables:
        table = entry["table"]
        if table in seen:
            raise ValueError(f"Duplicate golden: {table}")
        seen.add(table)
        unit = producers.get(table)
        if unit is None:
            results.append({"table": table, "status": "SKIP", "reason": "not yet migrated"})
            continue
        if ran_units is not None and unit not in ran_units:
            results.append({"table": table, "unit": unit, "status": "SKIP",
                            "reason": "not selected"})
            continue
        try:
            golden = read_golden(entry)
            actual = read_actual(entry)
            expected_hash, actual_hash = digest(golden), digest(actual)
            ok = len(golden) == len(actual) and expected_hash == actual_hash
            results.append({
                "table": table, "unit": unit, "status": "PASS" if ok else "FAIL",
                "golden_rows": len(golden), "actual_rows": len(actual),
                "golden_hash": expected_hash, "actual_hash": actual_hash,
                "reason": "matched" if ok else "row count or content mismatch",
            })
        except Exception as exc:
            results.append({"table": table, "unit": unit, "status": "FAIL", "reason": str(exc)})
    return {"status": "FAIL" if any(r["status"] == "FAIL" for r in results) else "PASS",
            "goldens": len(tables), "results": results}


class Warehouse:
    def __init__(self, client, warehouse_id):
        self.client = client
        self.warehouse_id = warehouse_id

    def sql(self, statement, parameters=None):
        options = {}
        if parameters is not None:
            from databricks.sdk.service.sql import StatementParameterListItem
            options["parameters"] = [StatementParameterListItem(name=k, value=v, type="STRING")
                                     for k, v in parameters.items()]
        response = self.client.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id, statement=statement, wait_timeout="50s", **options)
        while response.status.state.value in ("PENDING", "RUNNING"):
            time.sleep(1)
            response = self.client.statement_execution.get_statement(response.statement_id)
        if response.status.state.value != "SUCCEEDED":
            raise RuntimeError(str(response.status.error))
        rows = list(response.result.data_array or []) if response.result else []
        chunk = response.result.next_chunk_index if response.result else None
        while chunk is not None:
            result = self.client.statement_execution.get_statement_result_chunk_n(
                response.statement_id, chunk)
            rows.extend(result.data_array or [])
            chunk = result.next_chunk_index
        if response.manifest and response.manifest.truncated:
            raise RuntimeError("SQL result truncated")
        return rows


class GoldenStore:
    def __init__(self, client, path):
        self.client, self.path = client, path.rstrip("/")

    def manifest(self):
        from databricks.sdk.errors import NotFound
        # A missing volume is an infrastructure error, not an empty baseline.
        self.client.volumes.read("daiwt_01_mig_prod.landing.golden")
        try:
            response = self.client.files.download(self.path + "/manifest.json")
        except NotFound:
            return {"tables": []}
        with response.contents as stream:
            return json.load(stream)

    def rows(self, entry):
        path = entry["path"]
        if not path.startswith("outputs/") or ".." in path.split("/"):
            raise ValueError("Golden path must be under outputs/")
        response = self.client.files.download(self.path + "/" + path)
        with response.contents as stream:
            reader = csv.reader(io.StringIO(stream.read().decode("utf-8")))
            if next(reader) != entry["columns"]:
                raise ValueError("CSV header differs from manifest columns")
            rows = [[None if value == "\\N" else value for value in row] for row in reader]
        if any(len(row) != len(entry["columns"]) for row in rows):
            raise ValueError("CSV row width differs from manifest")
        return rows


def run(client, catalog, suffix, warehouse_id, registry, drop="D0", *, contract=None, ran_units=None,
        layer=None):
    if layer not in (None, "silver", "gold"):
        raise ValueError("Unsupported parity layer")
    if catalog not in ("daiwt_01_mig_dev", "daiwt_01_mig_prod"):
        raise ValueError("Unsupported catalog")
    identifier(suffix)
    store = GoldenStore(client, GOLDEN_PATH)
    warehouse = Warehouse(client, warehouse_id)

    def actual(entry):
        layer, table = entry["table"].split(".")
        if layer not in ("bronze", "silver", "gold"):
            raise ValueError("Unsupported output layer")
        schema = layer if catalog.endswith("_prod") else f"{layer}_{suffix}"
        columns = ", ".join(identifier(c) for c in entry["columns"])
        return warehouse.sql(f"SELECT {columns} FROM {identifier(catalog)}.{identifier(schema)}.{identifier(table)}")

    contract = contract if contract is not None else json.loads((ROOT / "src/parity/contract.json").read_text())
    validate(registry, contract)
    entries = contract_entries(contract, store.manifest(), drop)
    if layer is not None:
        entries = [entry for entry in entries if entry["table"].startswith(layer + ".")]
    result = compare({"tables": entries}, registry, store.rows, actual,
                     ran_units if catalog.endswith("_dev") else None)
    for row in result["results"]:
        row["drop"] = drop
    counts = {status: sum(row["status"] == status for row in result["results"])
              for status in ("PASS", "FAIL", "SKIP")}
    return {**result, "drop": drop, "counts": counts, "compared": counts["PASS"] + counts["FAIL"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="daiwt_01_mig_dev")
    parser.add_argument("--suffix", default="pr_1")
    parser.add_argument("--drop", default="D0")
    parser.add_argument("--units", "--ran-units", dest="ran_units", type=Path)
    parser.add_argument("--warehouse-id", default="6dc4b80538621b7b")
    parser.add_argument("--profile")
    parser.add_argument("--selected-units", default="*")
    parser.add_argument("--layer", choices=("silver", "gold"))
    parser.add_argument("--output", default="out/parity.json")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    try:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient(profile=args.profile)
        registry = load(ROOT)
        ran_units = json.loads(args.ran_units.read_text()) if args.ran_units else None
        if args.selected_units != "*":
            ran_units = args.selected_units.split(":") if args.selected_units else []
        if ran_units is not None and (not isinstance(ran_units, list)
                                      or not all(isinstance(unit, str) for unit in ran_units)):
            raise ValueError("ran units must be a list of unit names")
        result = run(client, args.catalog, args.suffix, args.warehouse_id, registry, args.drop,
                     ran_units=ran_units, layer=args.layer)
    except Exception as exc:
        result = {"status": "FAIL", "drop": args.drop, "goldens": 0, "results": [],
                  "counts": {"PASS": 0, "FAIL": 0, "SKIP": 0}, "compared": 0, "error": str(exc)}
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{result['status']} {args.drop}: {result['goldens']} goldens; "
          f"compared {result['compared']}; SKIP {result['counts']['SKIP']}")
    if result.get("error"):
        print(result["error"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    exit_code = main()
    if exit_code:
        raise SystemExit(exit_code)
