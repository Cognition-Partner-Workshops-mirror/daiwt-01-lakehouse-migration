import asyncio
import json
import pathlib

import yaml

CONFIG = json.loads(pathlib.Path.home().joinpath("migration_run.json").read_text())
REPO = CONFIG["repo"]
UNITS = yaml.safe_load(pathlib.Path(CONFIG["units_yml"]).read_text())["units"]

SCHEMA = {
    "type": "object",
    "properties": {
        "unit": {"type": "string"},
        "pr_url": {"type": "string"},
        "merged": {"type": "boolean"},
        "offline_parity": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["unit", "pr_url", "merged", "offline_parity"],
}


def brief(unit):
    upstreams = (
        "All Silver unit PRs are already merged to main. Start from updated main "
        "so your Silver upstreams are present. "
        if unit.get("layer") != "silver" else ""
    )
    return (
        f"Use the `dbx-migrate-unit` skill to migrate unit `{unit['name']}` in {REPO}.\n"
        f"Unit plan (from docs/units.yml):\n{json.dumps(unit, sort_keys=True, indent=2)}\n"
        "Touch only src/units/<unit>/. No Databricks deploys or job runs. "
        f"{upstreams}"
        "Use migration-ci fetch-goldens --units <unit> and migration-ci check --units <unit>. "
        "Merge your PR as soon as the `pr` workflow is green. "
        "Report merged=true only after GitHub confirms the PR is merged. Report the structured output."
    )


async def build(unit):
    try:
        return await agent(brief(unit), phase="silver" if unit.get("layer") == "silver" else "gold",
                           schema=SCHEMA, label=unit["name"],
                           repos=[REPO], soft_time_limit_minutes=20)
    except WorkflowAgentError as error:
        log(f"{unit['name']}: builder failed: {error}")
        return {"unit": unit["name"], "pr_url": "", "merged": False, "offline_parity": "ERROR"}


async def wave(units):
    results = await parallel([lambda u=u: build(u) for u in units]) if units else []
    for r in results:
        log(f"{r['unit']}: merged={r['merged']} parity={r['offline_parity']} {r['pr_url']}")
    return results


async def main():
    silver = [u for u in UNITS if u.get("layer") == "silver"]
    remaining = [u for u in UNITS if u.get("layer") != "silver"]
    await register_workflow({
        "name": "dbx-migrate-estate",
        "description": f"Parallel Silver builders, then parallel Gold builders for {REPO}",
        "phases": [{"title": "silver", "detail": "translate + offline parity + merge every Silver PR",
                    "labels": [u["name"] for u in silver]},
                   {"title": "gold", "detail": "all remaining units, after every Silver PR merged",
                    "labels": [u["name"] for u in remaining]}],
        "soft_time_limit_minutes": 40,
    })
    results = await wave(silver)
    merged = {r["unit"] for r in results if r.get("merged") is True}
    missing = [u["name"] for u in silver if u["name"] not in merged]
    if missing:
        log(f"SILVER NOT MERGED: {', '.join(missing)}; skipping Gold wave")
        log(f"SKIPPED: {', '.join(u['name'] for u in remaining)}")
        return
    results += await wave(remaining)
    merged = {r["unit"] for r in results if r.get("merged") is True}
    missing = [u["name"] for u in UNITS if u["name"] not in merged]
    log("ALL MERGED" if not missing else f"NOT MERGED: {', '.join(missing)}")


asyncio.run(main())
