# Verification policy and unit registry

Each unit is a directory `src/sql/units/<unit>/` containing all its SQL and
bundle resource YAML files plus `unit.json`. The directory name must match
`[a-z][a-z0-9_]*`. Every directory needs a registry file. Registry order is
alphabetical; execution is dependencies-first with alphabetical ties.

The frozen `unit.json` format has exactly these five keys:

```json
{
  "job": "silver_customers_job",
  "legacy_unit": "customer",
  "outputs": ["silver.customers"],
  "owns": ["bronze.customer_stage"],
  "depends_on": []
}
```

`job` is a bundle job key matching `[a-z][a-z0-9_]*`, normally unique and defined
in the unit directory's `*.job.yml`. The only sharing exception is a `silver_*`
entity with `job: silver_pipeline` and a direct `silver_pipeline` dependency:
its job is defined by the scaffold's YAML, conditionally augmented by
`silver_resources.py`. Entities need no job YAML. The runner executes each job
key once in dependency order; selecting entities refreshes all deployed entities
through the shared job, followed by visible Silver-layer parity.
`legacy_unit` names a source legacy unit
present in the parity contract outputs, or explicitly `foundation` for shared
units. Shared units publish no golden outputs; real output ownership must still
match that output's legacy unit. Every `outputs` table must be a
contract table belonging to that legacy unit. `owns` lists other written
bronze/silver/gold tables and must never contain contract tables. Each table
has exactly one owner across both lists and all units. Dependencies name
existing unit directories, with no cycles or self-dependencies. Bundle resources
are included by `src/sql/units/*/*.yml`; use bundle catalog/schema variables.

## Selection

The shared classifier diffs the merge-base of the PR base and head against head.
Changed or added files under a unit directory select that unit if it exists at
head. Removed units select nothing. Selection then adds transitive **upstream**
dependencies, never dependants. Explicit selections expand the same way; `*`
selects all units. Shared framework changes select all head units and require
verification only when selected units have golden outputs. `docs/`, `tests/`, `.github/`, `README.md`,
`.gitignore`, and `AGENTS.md` are ignored. Docs/test-only and removal-only changes
need no attestation.

## Fingerprints and builder

At a clean, pushed HEAD:

```bash
bash ci/dev_verify.sh pr_123
bash ci/dev_verify.sh pr_123 'silver_customers,another_unit'
bash ci/dev_verify.sh pr_123 '*'
```

The builder prints required and selected units, strictly validates and deploys
dev with only `schema_suffix`, cleans selected tables, runs selected jobs and
parity for **D0 only**, then records PASS/FAIL evidence in
`daiwt_01_mig_dev.default.unit_attestation`. Outputs without producers SKIP as
`not yet migrated`; unselected producers SKIP as `not selected`. Production
ignores selection and compares every registered output for every manifest drop.

The SHA-256 fingerprint covers sorted `<path>\t<blob sha>` lines for every file
in selected and transitive upstream unit directories plus every shared framework
file (tracked files outside unit directories, excluding ignored paths), followed
by the sorted expanded unit list. It is content-addressed, not commit-addressed:
rebasing over docs or unrelated unit additions preserves it, whereas shared or
upstream changes invalidate it.

PR CI never deploys. It queries PASS evidence by required-set fingerprint or PR
suffix, newest first, and accepts the first row with a superset of required units,
D0 PASS and a fingerprint matching the current head for that row's full unit set.
The check polls for up to 30 minutes; missing, malformed or stale evidence then
fails closed with a builder command. Selections without golden outputs skip
immediately without querying Unity Catalog.
The row also stores head SHA, suffix, status, drops, Markdown, run URLs, identity
and timestamp. Cleanup uses both ownership lists; landing is never a target.
