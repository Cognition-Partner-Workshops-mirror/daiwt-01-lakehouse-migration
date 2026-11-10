"""Build the nightly wrapper from registered units and their execution jobs."""
import json
import re
import sys
from pathlib import Path

from databricks.bundles.core import Resources
from databricks.bundles.jobs import Job

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'ci'))
from generate_silver_asof import manifest_drops
from registry import load, topo_order
from run_units import execution_jobs

LEGACY_ORDER = ('customer', 'transactions', 'dormant', 'monthly_balance',
                'customer_segment', 'branch_league', 'product_profitability',
                'channel', 'card_category', 'close_batch')
FOUNDATION = {'bronze_landing', 'sql_macros', 'silver_asof', 'silver_pipeline'}
VIEWS = {
    'v_customer_portfolio': ('silver.dim_customer', 'silver.dim_branch',
                             'silver.dim_account', 'gold.mart_customer_segment'),
    'v_credit_risk': ('silver.fct_loan', 'silver.dim_account',
                      'silver.dim_customer', 'silver.dim_branch'),
    'v_channel_trend': ('silver.dim_calendar', 'gold.mart_channel_daily'),
    'report_finance_monthly': ('gold.mart_monthly_balance',),
    'report_branch_league': ('gold.mart_branch_performance', 'silver.dim_branch'),
    'report_card_services': ('gold.mart_card_spend_category',),
    'report_risk_arrears': ('gold.v_credit_risk',),
}


def control_columns(contract):
    entries = [entry for entry in contract['outputs'] if entry['table'] == 'silver.etl_batch_control']
    if not entries:
        raise ValueError('Missing batch-control contract')
    columns = entries[0]['columns']
    if any(entry['columns'] != columns for entry in entries):
        raise ValueError('Conflicting batch-control contracts')
    result = []
    for column in columns:
        name, datatype = column['name'], column['source_type'].upper()
        if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
            raise ValueError('Invalid batch-control column')
        if re.fullmatch(r'(VAR)?CHAR\(\d+\)', datatype):
            datatype = 'STRING'
        elif datatype == 'INTEGER':
            datatype = 'INT'
        elif datatype.startswith('TIMESTAMP'):
            datatype = 'TIMESTAMP_NTZ'
        if not re.fullmatch(r'(STRING|DATE|BIGINT|INT|TIMESTAMP_NTZ|DECIMAL\(\d+,\d+\))', datatype):
            raise ValueError(f'Unsupported batch-control type: {datatype}')
        result.append(f'{name} {datatype}')
    return ', '.join(result)


def build_job(registry, manifest, contract, root=ROOT):
    ordered = execution_jobs(registry, topo_order(registry, registry))
    reserved = {'batch_open', 'batch_fail', 'release_export', 'reconcile', *VIEWS}
    if reserved.intersection(ordered):
        raise ValueError('Execution job collides with nightly framework task')
    metadata = json.dumps(manifest_drops(manifest), sort_keys=True)
    parameters = dict(catalog='${var.catalog}', silver_schema='${var.silver_schema}',
                      gold_schema='${var.gold_schema}', drop='{{job.parameters.drop}}',
                      manifest_drops=metadata, run_id='{{job.run_id}}')
    tasks, dependencies = {}, {}

    def sql_task(key, filename, needs=(), extra=None):
        tasks[key] = dict(task_key=key, max_retries=0, sql_task=dict(
            warehouse_id='${var.warehouse_id}', file=dict(
                path=str(Path(root) / 'src/orchestration' / filename), source='WORKSPACE'),
            parameters={**parameters, **(extra or {})}))
        dependencies[key] = set(needs)

    sql_task('batch_open', 'batch_open.sql', extra={'control_columns': control_columns(contract)})
    for job in ordered:
        tasks[job] = dict(task_key=job, max_retries=0, run_job_task=dict(
            job_id='${resources.jobs.' + job + '.id}',
            job_parameters={'drop': '{{job.parameters.drop}}'}))
        dependencies[job] = {'batch_open'}
    for unit, spec in registry.items():
        dependencies[spec['job']].update(registry[dep]['job'] for dep in spec['depends_on']
                                         if registry[dep]['job'] != spec['job'])
    foundations = FOUNDATION.intersection(ordered)
    for job in ordered:
        if job not in FOUNDATION and job != 'governance':
            dependencies[job].update(foundations)
    previous = None
    for legacy in LEGACY_ORDER:
        jobs = {spec['job'] for spec in registry.values()
                if spec['legacy_unit'] == legacy or spec['job'] == legacy}
        if len(jobs) > 1:
            raise ValueError(f'Multiple execution jobs for legacy step {legacy}')
        if jobs:
            job = jobs.pop()
            if previous and job != previous:
                dependencies[job].add(previous)
            previous = job

    owners = {table: spec['job'] for spec in registry.values()
              for table in spec['outputs'] + spec['owns']}
    controls = ('silver.etl_batch_control', 'silver.fct_transaction')
    if all(table in owners for table in controls):
        sql_task('release_export', 'release_export.sql', {owners[table] for table in controls})
        sql_task('reconcile', 'reconcile.sql', {'release_export'})
    for name, requirements in VIEWS.items():
        if all(table in owners for table in requirements):
            needs = {owners[table] for table in requirements}
            if 'reconcile' in tasks:
                needs.add('reconcile')
            sql_task(name, 'create_view.sql', needs,
                     {'view_sql': (Path(root) / 'src/orchestration/views' / f'{name}.sql').read_text()})
            owners['gold.' + name] = name
    if 'governance' in tasks:
        dependencies['governance'] = set(tasks) - {'governance'}
        tasks['governance']['run_if'] = 'NONE_FAILED'
    tasks['batch_fail'] = dict(task_key='batch_fail', max_retries=0,
                              run_if='AT_LEAST_ONE_FAILED', notebook_task=dict(
                                  notebook_path=str(Path(root) / 'src/orchestration/batch_fail.py'),
                                  source='WORKSPACE', base_parameters=parameters))
    dependencies['batch_fail'] = set(tasks) - {'batch_fail'}
    topo_order({key: {'depends_on': sorted(value)} for key, value in dependencies.items()}, dependencies)
    for key, task in tasks.items():
        if dependencies[key]:
            task['depends_on'] = [{'task_key': dep} for dep in sorted(dependencies[key])]
    return dict(name='${var.job_name_prefix}nvb_nightly',
                parameters=[{'name': 'drop', 'default': 'D0'}],
                schedule=dict(quartz_cron_expression='0 30 0 * * ?', timezone_id='Europe/Oslo',
                              pause_status='${var.nightly_pause_status}'),
                max_concurrent_runs=1, queue={'enabled': True},
                health={'rules': [{'metric': 'RUN_DURATION_SECONDS', 'op': 'GREATER_THAN', 'value': 9000}]},
                tags={'environment': '${bundle.target}', 'migration_framework': 'nightly'},
                tasks=list(tasks.values()))


def load_resources(bundle):
    resources = Resources()
    resources.add_resource('nvb_nightly', Job.from_dict(build_job(
        load(ROOT), json.loads((ROOT / 'golden/manifest.json').read_text()),
        json.loads((ROOT / 'src/parity/contract.json').read_text()))))
    return resources
