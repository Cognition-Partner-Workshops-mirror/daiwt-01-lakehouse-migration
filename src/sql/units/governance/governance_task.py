# Databricks notebook source
import sys

root = dbutils.widgets.get('bundle_root')
sys.path.insert(0, root + '/src/sql/units/governance')
from governance import apply_plan, build_plan, load_metadata, mismatches, read_state, summary

catalog = dbutils.widgets.get('catalog')
schemas = {layer: dbutils.widgets.get(layer + '_schema') for layer in ('bronze', 'silver', 'gold')}
registry, contract, metadata = load_metadata(root)
tables, tags, columns = read_state(spark, catalog, schemas)
plans = build_plan(registry, contract, catalog, schemas, dbutils.widgets.get('data_owner'),
                   tables, metadata, columns)
action = dbutils.widgets.get('action')
notes = {}
failures = []
if action == 'apply':
    for plan in plans:
        exemptions = apply_plan(spark, plan)
        if exemptions:
            notes[plan['table']] = exemptions
        if plan['status'] != 'SKIP':
            plan['status'] = 'APPLIED (pipeline metadata note)' if exemptions else 'APPLIED'
    dbutils.jobs.taskValues.set(key='pipeline_notes', value=notes)
elif action == 'check':
    notes = dbutils.jobs.taskValues.get(taskKey='apply_governance', key='pipeline_notes')
    for plan in plans:
        if plan['status'] == 'SKIP':
            continue
        exemptions = notes.get(plan['table'], []) if plan['pipeline'] else []
        missing = mismatches(plan, tables, tags, columns, exemptions)
        plan['status'] = 'FAIL: ' + ', '.join(missing) if missing else ('PASS (pipeline metadata note)' if exemptions else 'PASS')
        if missing:
            failures.append(f"{plan['table']}: {', '.join(missing)}")
else:
    raise ValueError(f'Unknown action: {action}')
markdown = summary(plans)
print(markdown)
dbutils.jobs.taskValues.set(key='summary', value=markdown)
if failures:
    raise RuntimeError('Governance check failed: ' + '; '.join(failures))
