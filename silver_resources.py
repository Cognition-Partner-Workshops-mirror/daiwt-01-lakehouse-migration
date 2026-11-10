"""Discover Silver pipeline libraries without a shared entity list."""
from pathlib import Path

from databricks.bundles.core import Resources
from databricks.bundles.pipelines import Pipeline


def load_resources(bundle):
    root = Path(__file__).parent
    scaffold = root / "src/sql/units/silver_pipeline"
    files = [
        path for path in sorted((root / "src/sql/units").glob("silver_*/*.pipeline.sql"))
        if path.parent != scaffold
    ]
    resources = Resources()
    if not files:
        return resources
    resources.add_resource("silver", Pipeline.from_dict({
        "name": "${var.job_name_prefix}silver",
        "serverless": True,
        "catalog": "${var.catalog}",
        "schema": "${var.silver_schema}",
        "libraries": [{"file": {"path": str(path.relative_to(root))}} for path in files],
        "parameters": {"drop": "D0"},
        "configuration": {
            "catalog": "${var.catalog}", "silver_schema": "${var.silver_schema}",
            "drop": "D0", "silver_bad_rows": "${var.silver_bad_rows}",
        },
    }))
    return resources
