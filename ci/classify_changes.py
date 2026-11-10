"""Select registered SQL units affected by a change."""
import json
import subprocess
import sys
from registry import load, topo_order


def expand(registry, units):
    if units == "*" or "*" in units:
        return topo_order(registry, registry)
    selected = set(units)
    if selected - registry.keys():
        raise ValueError("unknown units: " + ", ".join(sorted(selected - registry.keys())))
    while True:
        dependants = {unit for unit, spec in registry.items() if selected.intersection(spec.get("depends_on", []))}
        if dependants <= selected:
            return topo_order(registry, selected)
        selected |= dependants


def classify(registry, paths):
    selected = set()
    for path in paths:
        if path.startswith(("tests/", "README", "docs/", ".github/")):
            continue
        matches = {unit for unit in registry if path in (f"src/sql/units/{unit}.sql", f"resources/{unit}.job.yml")
                   or path.startswith(f"src/sql/units/{unit}/")}
        if not matches or path == "src/sql/units/registry.json":
            return topo_order(registry, registry)
        selected |= matches
    return expand(registry, selected)


if __name__ == "__main__":
    registry = load()
    paths = subprocess.check_output(
        ["git", "diff", "--name-only", sys.argv[1], sys.argv[2]], text=True).splitlines()
    print(json.dumps(classify(registry, paths)))
