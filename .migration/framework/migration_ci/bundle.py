from pathlib import Path

from databricks.bundles.core import Resources
from databricks.bundles.jobs import Job

from .model import discover, estate


def load_resources(bundle):
    root = Path.cwd()
    config = estate(root)
    units, _, graph = discover(root)
    keys = {n: [f'{n}__{i + 1}' for i in range(len(u['sql']))] for n, u in units.items()}
    script = '.migration/framework/migration_ci/runner.py'
    def python_task(key, action, dependencies):
        return {'task_key': key, 'depends_on': [{'task_key': d} for d in dependencies],
                'environment_key': 'migration', 'spark_python_task': {
                    'python_file': script,
                    'parameters': [action, '--catalog', '${var.catalog}', '--drop', '{{job.parameters.drop}}',
                                   '--run-id', '{{job.run_id}}', '--schema-suffix', '${var.schema_suffix}']}}
    tasks = [python_task('land_bronze', 'land', [])]
    for name, unit in units.items():
        for i, file in enumerate(unit['sql']):
            deps = [keys[name][i - 1]] if i else [keys[n][-1] for n in sorted(graph[name])] or ['land_bronze']
            tasks.append({'task_key': keys[name][i], 'depends_on': [{'task_key': d} for d in deps],
                          'sql_task': {'warehouse_id': '${var.warehouse_id}',
                                       'file': {'path': str((unit['directory'] / file).relative_to(root))},
                                       'parameters': {'catalog': '${var.catalog}', 'bronze_schema': 'bronze${var.schema_suffix}',
                                                      'silver_schema': 'silver${var.schema_suffix}', 'gold_schema': 'gold${var.schema_suffix}',
                                                      'drop': '{{job.parameters.drop}}', 'golden_path': '${var.golden_path}'}}})
    tasks.append(python_task('parity', 'parity', [v[-1] for v in keys.values()] or ['land_bronze']))
    tasks[-1]['run_if'] = 'ALL_DONE'
    resources = Resources()
    resources.add_resource('migration', Job.from_dict({
        'name': '[${bundle.target}${var.schema_suffix}] migration', 'max_concurrent_runs': 1,
        'parameters': [{'name': 'drop', 'default': config['drops'][0]}],
        'environments': [{'environment_key': 'migration', 'spec': {'environment_version': '2',
                         'dependencies': ['PyYAML>=6,<7', 'sqlglot>=26,<29', 'duckdb>=1.2,<2', 'databricks-sdk>=0.85,<1']}}],
        'tasks': tasks,
    }))
    return resources
