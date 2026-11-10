"""Discover Silver pipeline libraries without a shared entity list."""
from pathlib import Path

from databricks.bundles.core import Resources, job_mutator
from databricks.bundles.jobs import Job
from databricks.bundles.pipelines import Pipeline


def pipeline_files():
    root = Path(__file__).parent
    scaffold = root / "src/sql/units/silver_pipeline"
    return [
        path for path in sorted((root / "src/sql/units").glob("silver_*/*.pipeline.sql"))
        if path.parent != scaffold
    ]


def load_resources(bundle):
    root = Path(__file__).parent
    files = pipeline_files()
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
            "catalog": "${var.catalog}", "bronze_schema": "${var.bronze_schema}",
            "silver_schema": "${var.silver_schema}",
            "drop": "D0", "silver_bad_rows": "${var.silver_bad_rows}",
        },
    }))
    return resources


@job_mutator
def configure_silver_job(bundle, job):
    if (job.tags or {}).get('migration_unit') != 'silver_pipeline' or not pipeline_files():
        return job
    root = Path(__file__).parent
    definition = job.as_dict()
    verify = definition['tasks'][0]
    verify['spark_python_task']['python_file'] = str(root / 'src/parity/parity_check.py')
    verify['depends_on'] = [{'task_key': 'refresh_silver'}]
    definition['tasks'].insert(0, {
        'task_key': 'refresh_silver',
        'pipeline_task': {
            'pipeline_id': '${resources.pipelines.silver.id}',
            'full_refresh': True,
            'parameters': {'drop': '{{job.parameters.drop}}'},
        },
    })
    return Job.from_dict(definition)
