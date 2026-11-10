# Governance

`governance` applies and then independently checks Unity Catalog comments, column
comments, tags and optional ownership. It discovers `outputs` and `owns` from all
unit registries, reads Teradata `source_type` from the parity contract, and uses
`legacy_metadata.json` for captured legacy DDL comments and nightly owner teams.
The metadata snapshot records its source paths; runtime needs no legacy checkout.
Bronze names map `edw_*`/`stg_*` to `nvb_edw.*`/`nvb_stg.*`. Silver and Gold map
to `nvb_edw.*` and `nvb_mart.*`. Framework tables and business views are included
when present, even without registry ownership. Registered producers take priority.

Business teams become `business_owner` tags, never UC principals. The job-local
`data_owner` parameter defaults empty, preserving existing UC owners. An explicit
principal enables ownership changes; no grants are performed. Missing objects are
reported as SKIP. Unsupported pipeline comment/column/owner operations are recorded
and exempted from checking; tags and other errors still fail. Metadata mismatches
fail `check_governance`.

This unit has no golden outputs or dependencies. `nvb_nightly` automatically runs
it last with `NONE_FAILED`, after every non-failure task. Standalone runs may follow
`bronze_landing` to govern just the available Bronze objects.
