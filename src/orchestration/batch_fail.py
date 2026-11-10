# Databricks notebook source
import json

from databricks.sdk import WorkspaceClient

for name in ('catalog', 'silver_schema', 'run_id', 'drop', 'manifest_drops'):
    dbutils.widgets.text(name, '')
values = {name: dbutils.widgets.get(name) for name in
          ('catalog', 'silver_schema', 'run_id', 'drop', 'manifest_drops')}
metadata = json.loads(values['manifest_drops']).get(values['drop'])
if not metadata:
    raise ValueError('Unknown captured drop')
schema = '.'.join('`' + values[name].replace('`', '``') + '`'
                  for name in ('catalog', 'silver_schema'))
run = WorkspaceClient().jobs.get_run(run_id=int(values['run_id']))
failed = sorted((task for task in run.tasks or []
                 if task.state and task.state.result_state
                 and task.state.result_state.value in ('FAILED', 'TIMEDOUT')),
                key=lambda task: (task.start_time or 0, task.task_key))
step = failed[0].task_key if failed else 'batch_fail'
args = dict(run_id=values['run_id'], batch_date=metadata['business_date'],
            batch_id=metadata['batch_id'], step=step)
admitted = spark.sql(f"""SELECT count(*) AS n FROM {schema}.nightly_run_state
    WHERE run_id = :run_id AND batch_id = :batch_id""", args=args).first()['n']
if admitted:
    spark.sql(f"""UPDATE {schema}.etl_batch_control
        SET status = 'FAILED', last_step_cd = :step
        WHERE batch_date = CAST(:batch_date AS DATE) AND batch_id = :batch_id
          AND job_name = 'NIGHTLY'""", args=args).collect()
    spark.sql(f"""UPDATE {schema}.nightly_run_state SET status = 'FAILED'
        WHERE run_id = :run_id""", args=args).collect()
print(f"NIGHTLY failure handler: {step}; admitted={bool(admitted)}")
