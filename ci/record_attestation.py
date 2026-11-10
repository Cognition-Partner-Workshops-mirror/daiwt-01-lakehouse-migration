"""Record pushed-head verification evidence."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from parity.parity_check import Warehouse, identifier
from attest import render
from classify_changes import fingerprint

TABLE = "daiwt_01_mig_dev.default.unit_attestation"
COLUMNS = ("fingerprint", "head_sha", "suffix", "status", "units", "selection", "drops", "attestation_md", "run_urls", "recorded_by", "recorded_at")


def table_name(value):
    parts = value.split(".")
    if len(parts) != 3:
        raise ValueError("expected catalog.schema.table")
    return ".".join(identifier(part) for part in parts)


def main():
    from databricks.sdk import WorkspaceClient
    parser = argparse.ArgumentParser()
    for name in ("head-sha", "suffix", "status", "units", "selection"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--table", default=TABLE)
    parser.add_argument("--attestation", default="attestation.md")
    parser.add_argument("--run-urls", default="out/run-urls.txt")
    args = parser.parse_args()
    drops = {}
    for drop in ['D0']:
        try:
            result = json.loads(Path(f"out/parity-{drop}.json").read_text())
            valid = render(result)[1] and result["drop"] == drop
            drops[drop] = {"status": "PASS" if valid else "FAIL", "counts": result["counts"]}
        except (OSError, ValueError, KeyError, TypeError):
            drops[drop] = {"status": "FAIL", "counts": {}}
    status = "PASS" if args.status == "PASS" and all(v["status"] == "PASS" for v in drops.values()) else "FAIL"
    client = WorkspaceClient()
    warehouse = Warehouse(client, "6dc4b80538621b7b")
    table = table_name(args.table)
    warehouse.sql(f"CREATE TABLE IF NOT EXISTS {table} (" + ", ".join(
        f"{column} {'TIMESTAMP' if column == 'recorded_at' else 'STRING'}" for column in COLUMNS) + ") USING DELTA")
    values = dict(fingerprint=fingerprint(args.head_sha, json.loads(Path(args.units).read_text())),
                  head_sha=args.head_sha, suffix=args.suffix, status=status,
                  units=json.dumps(json.loads(Path(args.units).read_text())), selection=args.selection,
                  drops=json.dumps(drops), attestation_md=Path(args.attestation).read_text(),
                  run_urls=json.dumps(Path(args.run_urls).read_text().splitlines()),
                  recorded_by=client.current_user.me().user_name)
    warehouse.sql(f"INSERT INTO {table} (" + ", ".join(COLUMNS) + ") VALUES (" +
                  ", ".join(":" + column for column in COLUMNS[:-1]) + ", current_timestamp())", values)
    print(f"Recorded {status} {args.head_sha} {args.suffix}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
