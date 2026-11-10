"""Run registered job keys in a bundle target."""
import argparse
import json
import re
import subprocess
from pathlib import Path
from registry import load, topo_order

def deployed_jobs(target, suffix):
    summary = json.loads(subprocess.check_output([
        "databricks", "bundle", "summary", "-t", target, "--var",
        f"schema_suffix={suffix}", "--output", "json"], text=True))
    return summary.get("resources", {}).get("jobs", {})

def execution_jobs(registry, selected):
    pending = {registry[unit]['job']: set() for unit in selected}
    for unit in selected:
        job = registry[unit]['job']
        pending[job].update(registry[dependency]['job']
                            for dependency in registry[unit].get('depends_on', [])
                            if dependency in selected and registry[dependency]['job'] != job)
    ordered = []
    while pending:
        ready = next((job for job, dependencies in pending.items() if not dependencies.intersection(pending)), None)
        if ready is None:
            raise ValueError('dependency cycle between shared jobs: ' + ', '.join(pending))
        ordered.append(ready)
        del pending[ready]
    return ordered

def run_units(suffix, registry, units=None, target="dev", drop=None, run_urls=None):
    if target not in ("dev", "prod"):
        raise ValueError("invalid target")
    if (target == "dev" and not re.fullmatch(r"pr_[0-9]+", suffix)) or (target == "prod" and suffix != "prod"):
        raise ValueError("invalid suffix for target")
    if not isinstance(drop, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", drop):
        raise ValueError("invalid drop")
    params = {"drop": drop}
    urls = []
    selected = topo_order(registry, registry if units is None else units)
    ordered_jobs = execution_jobs(registry, selected)
    jobs = deployed_jobs(target, suffix) if ordered_jobs else {}
    for job in ordered_jobs:
        if not jobs.get(job, {}).get("id"):
            raise ValueError(f"job not deployed in bundle state: {job}")
        command = ["databricks", "bundle", "run", "-t", target, "--var",
                   f"schema_suffix={suffix}", "--params", ",".join(f"{k}={v}" for k, v in params.items()), job]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                if "Run URL" in line:
                    url = line.strip()
                    urls.append(url)
                    if run_urls:
                        with Path(run_urls).open("a") as output:
                            output.write(url + "\n")
            if process.wait():
                raise subprocess.CalledProcessError(process.returncode, command)
    return urls


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("suffix")
    parser.add_argument("units", nargs="?")
    parser.add_argument("--target", choices=("dev", "prod"), default="dev")
    parser.add_argument("--drop", required=True)
    parser.add_argument("--run-urls")
    args = parser.parse_args()
    units = json.loads(Path(args.units).read_text()) if args.units else None
    registry = load()
    run_units(args.suffix, registry, units, args.target, args.drop, args.run_urls)
