"""Require current pushed-head evidence before merging."""
import argparse
import json
import subprocess
from pathlib import Path

from classify_changes import classify
from drops import load_drops
from registry import validate
from record_attestation import COLUMNS, TABLE, Warehouse, table_name


def evaluate(rows, head_sha, suffix, required_units, expected_drops):
    if not rows:
        return False, "no attestation for this head and suffix"
    row = rows[0]
    if row.get("head_sha") != head_sha or row.get("suffix") != suffix:
        return False, "head or suffix mismatch"
    if row.get("status") != "PASS":
        return False, "latest attestation is not PASS"
    try:
        drops = json.loads(row["drops"])
        units = json.loads(row["units"])
        if not isinstance(drops, dict) or set(drops) != set(expected_drops) or any(drops[d]["status"] != "PASS" for d in expected_drops):
            return False, "missing or failed replay drop"
        if not isinstance(units, list) or not all(isinstance(unit, str) for unit in units):
            return False, "invalid recorded units"
        if not set(required_units) <= set(units):
            return False, "insufficient recorded units"
        if not isinstance(row["attestation_md"], str) or not row["attestation_md"]:
            return False, "missing attestation markdown"
    except (ValueError, KeyError, TypeError):
        return False, "invalid attestation data"
    return True, "PASS"


def main():
    from databricks.sdk import WorkspaceClient
    parser = argparse.ArgumentParser()
    for name in ("head-sha", "base-sha", "suffix"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--table", default=TABLE)
    parser.add_argument("--output", default="attestation.md")
    args = parser.parse_args()
    try:
        registry = json.loads(subprocess.check_output(["git", "show", f"{args.head_sha}:src/sql/units/registry.json"], text=True))
        validate(registry)
        paths = subprocess.check_output(["git", "diff", "--name-only", f"{args.base_sha}...{args.head_sha}"], text=True).splitlines()
        warehouse = Warehouse(WorkspaceClient(), "6dc4b80538621b7b")
        data = warehouse.sql(f"SELECT {', '.join(COLUMNS)} FROM {table_name(args.table)} WHERE head_sha = :head AND suffix = :suffix ORDER BY recorded_at DESC",
                             {"head": args.head_sha, "suffix": args.suffix})
        rows = [dict(zip(COLUMNS, row)) for row in data]
        ok, reason = evaluate(rows, args.head_sha, args.suffix, classify(registry, paths), load_drops())
    except Exception as exc:
        ok, reason = False, f"attestation query or selection failed: {exc}"
    body = f"# DAIWT-01 attestation\n\n**FAIL** — {reason}\n"
    if ok:
        row = rows[0]
        body = f"Head {args.head_sha} — {row['recorded_by']} — {row['recorded_at']}\n\n" + row["attestation_md"]
    Path(args.output).write_text(body)
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
