# Unit registry

`src/sql/units/registry.json` is currently `{}`: no migrated units.
Each future unit has exactly four fields: `job` (unique bundle job key),
`outputs` (golden output table names), `owns` (other owned medallion tables),
and `depends_on` (registered upstream unit names).

Unit and table identifiers use lowercase letters, digits and underscores.
Tables use unsuffixed `bronze`, `silver` or `gold` layers. Each table has one
owner. Dependencies must exist and form an acyclic graph.

Classification expands to dependants; execution orders selected units
dependencies-first, breaking ties by registry order. Shared framework changes
select all units. Documentation, test and workflow changes select none.

Cleanup uses both ownership lists: `--suffix pr_123 --units a,b` (or `*`)
drops selected dev objects; `--prod` drops registered objects without destroying
schemas. Landing volumes are never cleanup targets.
