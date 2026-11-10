"""Validate unit ownership and order selected units by dependency."""
import json
import re
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
        ready = next((unit for unit in registry if unit in selected
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


def validate(registry, contract_outputs=None):
    if not isinstance(registry, dict):
        raise ValueError("registry must be a JSON object")
    jobs, owners = set(), {}
    contract_outputs = set(contract_outputs) if contract_outputs is not None else None
    for unit, spec in registry.items():
        if not isinstance(unit, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", unit):
            raise ValueError(f"invalid unit name: {unit!r}")
        if not isinstance(spec, dict) or set(spec) != {"job", "outputs", "owns", "depends_on"}:
            raise ValueError(f"{unit}: required keys are exactly job, outputs, owns, depends_on")
        job = spec["job"]
        if not isinstance(job, str) or not job.strip():
            raise ValueError(f"{unit}: job must be a non-empty string")
        if job in jobs:
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


def load(path="src/sql/units/registry.json"):
    registry = json.loads(Path(path).read_text())
    validate(registry)
    return registry
