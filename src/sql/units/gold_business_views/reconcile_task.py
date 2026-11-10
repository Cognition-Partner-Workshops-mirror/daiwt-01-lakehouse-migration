# Databricks notebook source
import sys

root = dbutils.widgets.get('bundle_root')
sys.path.insert(0, root + '/src/sql/units/gold_business_views')
from business_views_checks import markdown, run


def query(sql):
    return [tuple(row) for row in spark.sql(sql).collect()]


def schema(sql):
    return [(field.name, field.dataType.simpleString()) for field in spark.sql(sql).schema.fields]


widget = dbutils.widgets.get
results = run(query, schema, widget('catalog'), widget('silver_schema'), widget('gold_schema'),
              widget('drop'), widget('manifest_drops'), widget('as_of_date'), widget('window_start'),
              widget('report_year'))
summary = markdown(results)
print(summary)
dbutils.jobs.taskValues.set(key='summary', value=summary)
failures = [r['check'] for r in results if r['status'] != 'PASS']
if failures:
    raise RuntimeError('Business view reconciliation failed: ' + '; '.join(failures))
dbutils.notebook.exit(summary)
