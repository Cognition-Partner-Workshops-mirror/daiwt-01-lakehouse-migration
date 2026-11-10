"""Validate unit ownership and order selected units by dependency."""
import json
import re
import subprocess
from pathlib import Path


def tables(spec):
    return spec["outputs"] + spec["owns"]


def topo_order(registry, units):
    selected = set(registry if units == "*" else units)
    unknown = selected - registry.keys()
    if unknown:
        raise ValueError("unknown units: " + ", ".join(sorted(unknown)))
    ordered = []
    while selected:
        ready = next((unit for unit in sorted(registry) if unit in selected
                      and not selected.intersection(registry[unit].get("depends_on", []))), None)
        if ready is None:
            raise ValueError("dependency cycle among: " + ", ".join(unit for unit in registry if unit in selected))
        ordered.append(ready)
        selected.remove(ready)
    return ordered


def with_dependencies(registry, units):
    selected = set(units)
    topo_order(registry, selected)
    while True:
        upstream = {dependency for unit in selected for dependency in registry[unit].get("depends_on", [])}
        if upstream <= selected:
            return topo_order(registry, selected)
        selected |= upstream


def validate(registry, contract):
    if not isinstance(registry, dict):
        raise ValueError("registry must be a JSON object")
    jobs, owners = set(), {}
    contract_outputs = {entry['table']: entry['unit'] for entry in contract['outputs']}
    for unit, spec in registry.items():
        if not isinstance(unit, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", unit):
            raise ValueError(f"invalid unit name: {unit!r}")
        if not isinstance(spec, dict) or set(spec) != {"job", "legacy_unit", "outputs", "owns", "depends_on"}:
            raise ValueError(f"{unit}: required keys are exactly job, legacy_unit, outputs, owns, depends_on")
        if not isinstance(spec['legacy_unit'], str) or spec['legacy_unit'] not in set(contract_outputs.values()):
            raise ValueError(f"{unit}: legacy_unit not in contract")
        job = spec["job"]
        if not isinstance(job, str) or not job.strip():
            raise ValueError(f"{unit}: job must be a non-empty string")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", job):
            raise ValueError(f"{unit}: invalid job identifier: {job}")
        if job == 'silver_pipeline' and unit != 'silver_pipeline':
            if (not unit.startswith('silver_') or not isinstance(spec['depends_on'], list)
                    or 'silver_pipeline' not in spec['depends_on']):
                raise ValueError(f'{unit}: shared silver_pipeline job requires a silver entity depending on silver_pipeline')
        if job in jobs and job != 'silver_pipeline':
            raise ValueError(f"duplicate job: {job}")
        jobs.add(job)
        for field in ("outputs", "owns", "depends_on"):
            values = spec[field]
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ValueError(f"{unit}: {field} must be a list of strings")
            if field == "depends_on":
                continue
            if len(values) != len(set(values)):
                raise ValueError(f"{unit}: duplicate table in {field}")
            for table in values:
                if not re.fullmatch(r"(bronze|silver|gold)\.[a-z][a-z0-9_]*", table):
                    raise ValueError(f"{unit}: invalid table name: {table}")
                if field == "outputs" and contract_outputs is not None and table not in contract_outputs:
                    raise ValueError(f"{unit}: output not in contract: {table}")
                if field == 'outputs' and contract_outputs[table] != spec['legacy_unit']:
                    raise ValueError(f"{unit}: output belongs to another legacy_unit: {table}")
                if field == "owns" and contract_outputs is not None and table in contract_outputs:
                    raise ValueError(f"{unit}: contract output belongs in outputs, not owns: {table}")
                if table in owners:
                    raise ValueError(f"table {table} owned by both {owners[table]} and {unit}")
                owners[table] = unit
        for dependency in spec["depends_on"]:
            if dependency == unit:
                raise ValueError(f"{unit}: self-dependency")
            if dependency not in registry:
                raise ValueError(f"{unit}: unknown dependency: {dependency}")
    visited, active = set(), []

    def visit(unit):
        if unit in active:
            raise ValueError("dependency cycle: " + " -> ".join(active[active.index(unit):] + [unit]))
        if unit in visited:
            return
        active.append(unit)
        for dependency in registry[unit]["depends_on"]:
            visit(dependency)
        active.pop()
        visited.add(unit)

    for unit in registry:
        visit(unit)


def load(root="."):
    root = Path(root)
    registry = {directory.name: json.loads((directory / 'unit.json').read_text())
                for directory in sorted((root / 'src/sql/units').glob('*')) if directory.is_dir()}
    validate(registry, json.loads((root / 'src/parity/contract.json').read_text()))
    return registry


def git(*args):
    return subprocess.check_output(['git', *args], text=True)


def load_at(rev):
    prefix = 'src/sql/units/'
    paths = git('ls-tree', '-r', '--name-only', rev, '--', prefix).splitlines()
    directories = sorted({path[len(prefix):].split('/')[0] for path in paths
                          if '/' in path[len(prefix):]})
    registry = {unit: json.loads(git('show', f'{rev}:{prefix}{unit}/unit.json'))
                for unit in directories}
    validate(registry, json.loads(git('show', f'{rev}:src/parity/contract.json')))
    return registry
