"""Generate explicit parity targets from captured outputs and unit metadata."""
import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def build(manifest, units):
    outputs = []
    paths, tables = set(), set()
    for entry in manifest["files"]:
        if not entry["path"].startswith("outputs/"):
            continue
        parts = entry["path"].split("/")
        if len(parts) != 4 or ".." in parts:
            raise ValueError(f"invalid output path: {entry['path']}")
        _, drop, unit, filename = parts
        drops = [d["name"] if isinstance(d, dict) else d for d in manifest["drops"]]
        if drop not in drops:
            raise ValueError(f"undeclared output drop: {drop}")
        matches = [output for output in units[unit]["outputs"]
                   if Path(output["golden"]).name == filename]
        if len(matches) != 1:
            raise ValueError(f"expected one unit output for {entry['path']}")
        output = matches[0]
        if "table" in output:
            table = entry["table"]
            if table != output["table"]:
                raise ValueError(f"target table mismatch: {entry['path']}")
            kind = "table"
        elif "target_sql" in output and filename == "report.csv":
            table, kind = f"gold.report_{unit}", "report"
        else:
            raise ValueError(f"unsupported output: {entry['path']}")
        if entry["path"] in paths or table in tables:
            raise ValueError(f"duplicate output path or target table: {entry['path']}")
        if entry["keys"] != output["keys"]:
            raise ValueError(f"output keys mismatch: {entry['path']}")
        paths.add(entry["path"])
        tables.add(table)
        outputs.append(dict(path=entry["path"], drop=drop, unit=unit, table=table, kind=kind,
                            **{key: entry[key] for key in ("keys", "sha256", "row_count", "columns")}))
    return {"outputs": outputs}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "golden/manifest.json")
    parser.add_argument("--units", type=Path,
                        required=True, help="YAML unit metadata with explicit target tables and golden paths")
    parser.add_argument("--output", type=Path, default=ROOT / "src/parity/contract.json")
    args = parser.parse_args()
    contract = build(json.loads(args.manifest.read_text()), yaml.safe_load(args.units.read_text())["units"])
    args.output.write_text(json.dumps(contract, indent=2) + "\n")
    entries = contract["outputs"]
    print(f"Contract: {len(entries)} outputs; "
          f"{sum(e['kind'] == 'table' for e in entries)} tables; "
          f"{sum(e['kind'] == 'report' for e in entries)} reports")


if __name__ == "__main__":
    main()
