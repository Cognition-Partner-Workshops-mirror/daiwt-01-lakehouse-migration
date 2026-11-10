"""Remove only this bundle's PR schemas or registered production outputs."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from parity.parity_check import Warehouse, identifier
from registry import load, tables, topo_order

MARKER = "daiwt-01-migration"
REPO = "Cognition-Partner-Workshops-mirror/daiwt-01-lakehouse-migration"


def candidates(schemas, closed, suffix=None):
    # The optional pr_<n>_ prefix matches schemas deployed while presets.name_prefix renamed them.
    for schema in schemas:
        match = re.fullmatch(r"(?:pr_([0-9]+)_)?(bronze|silver|gold)_pr_([0-9]+)", schema.name)
        if (match and match[1] in (None, match[3]) and schema.comment == MARKER
                and (closed is None or int(match[3]) in closed)
                and (suffix is None or suffix == "pr_" + match[3])):
            yield schema.name


def drop_tables(warehouse, registry, units=None, suffix=None, prod=False):
    if prod and suffix is not None:
        raise ValueError("production cleanup does not accept a suffix")
    if not prod and (not isinstance(suffix, str) or not re.fullmatch(r"pr_[0-9]+", suffix)):
        raise ValueError("invalid PR suffix")
    catalog = "daiwt_01_mig_prod" if prod else "daiwt_01_mig_dev"
    selected = topo_order(registry, registry if prod or units is None else units)
    owned = [table for unit in selected for table in tables(registry[unit])]
    schemas = {layer: layer if prod else f"{layer}_{suffix}" for layer in ("bronze", "silver", "gold")}
    types = {"TABLE": "TABLE", "MANAGED": "TABLE", "EXTERNAL": "TABLE", "VIEW": "VIEW",
             "MATERIALIZED_VIEW": "MATERIALIZED VIEW", "STREAMING_TABLE": "TABLE"}
    for layer in ("bronze", "silver", "gold"):
        names = [table.split(".")[1] for table in owned if table.split(".")[0] == layer]
        if not names:
            continue
        rows = warehouse.sql(f"SELECT table_name, table_type FROM {identifier(catalog)}.information_schema.tables "
                             f"WHERE table_schema = '{schemas[layer]}'")
        objects = dict(rows)
        for name in names:
            if name not in objects:
                continue
            kind = objects[name]
            if kind not in types:
                raise ValueError(f"unsupported table type: {kind}")
            full = ".".join(identifier(v) for v in (catalog, schemas[layer], name))
            try:
                warehouse.sql(f"DROP {types[kind]} IF EXISTS {full}")
            except Exception as exc:
                message = str(exc).lower()
                if "pipeline" in message and any(term in message for term in ("managed by", "owned by", "defined in")):
                    print(f"SKIP {full}: managed by a Lakeflow pipeline")
                else:
                    raise


def cleanup_pr(warehouse, suffix, schemas):
    if not isinstance(suffix, str) or not re.fullmatch(r"pr_[0-9]+", suffix):
        raise ValueError("invalid PR suffix")
    errors = []
    for name in candidates(schemas, None, suffix):
        try:
            warehouse.sql(f"DROP SCHEMA IF EXISTS `daiwt_01_mig_dev`.{identifier(name)} CASCADE")
            print(f"Dropped schema daiwt_01_mig_dev.{name}")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    try:
        subprocess.run(["databricks", "bundle", "destroy", "-t", "dev", "--var",
                        f"schema_suffix={suffix}", "--auto-approve"], check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        errors.append(f"bundle destroy {suffix}: {exc}")
    return errors


def main():
    from databricks.sdk import WorkspaceClient
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true")
    group.add_argument("--prod", action="store_true")
    group.add_argument("--suffix")
    parser.add_argument("--units")
    args = parser.parse_args()
    if args.suffix and not re.fullmatch(r"pr_[0-9]+", args.suffix):
        parser.error("invalid PR suffix")
    if args.units is not None and not args.suffix:
        parser.error("--units requires --suffix")
    registry = load()
    units = topo_order(registry, args.units.split(",")) if args.units is not None and args.units != "*" else list(registry)
    client = WorkspaceClient()
    warehouse = Warehouse(client, "6dc4b80538621b7b")
    if args.prod or args.units is not None:
        drop_tables(warehouse, registry, units, args.suffix, args.prod)
        return
    closed = None
    if not args.all and not args.suffix:
        pages = json.loads(subprocess.check_output(
            ["gh", "api", "--paginate", "--slurp", f"repos/{REPO}/pulls?state=closed&per_page=100"],
            text=True))
        closed = {pr["number"] for page in pages for pr in page}
    schemas = list(client.schemas.list(catalog_name="daiwt_01_mig_dev"))
    suffixes = sorted({"pr_" + name.rsplit("_", 1)[1] for name in candidates(schemas, closed, args.suffix)})
    errors = []
    for suffix in suffixes:
        errors.extend(cleanup_pr(warehouse, suffix, schemas))
    if errors:
        raise RuntimeError("PR cleanup failed: " + "; ".join(errors))


if __name__ == "__main__":
    main()
