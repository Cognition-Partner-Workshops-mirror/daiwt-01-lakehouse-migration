import argparse
import json
import re
from pathlib import Path

from migration_ci.goldens import column_type, columns, entries, input_entries, input_table, output_path, read_csv
from migration_ci.model import discover, estate, schema_name
from migration_ci.offline import compare, summary


def ident(value):
    if not re.fullmatch(r'[a-z][a-z0-9_]*', value):
        raise ValueError(f'Invalid SQL identifier: {value}')
    return f'`{value}`'


def qualified(catalog, table, suffix=''):
    layer, name = table.split('.')
    return '.'.join(ident(part) for part in (catalog, schema_name(layer, suffix), name))


def replace_drop(frame, table, drop):
    if not re.fullmatch(r'D[0-9]+', drop):
        raise ValueError('Invalid drop')
    frame.write.format('delta').mode('overwrite').option('replaceWhere', f"_drop = '{drop}'").saveAsTable(table)


def run(action, root, catalog, drop, run_id, schema_suffix=''):
    from databricks.sdk import WorkspaceClient
    from pyspark.sql import SparkSession, functions as F
    from pyspark.sql.types import StringType, StructField, StructType
    config = estate(root)
    if catalog not in (config['dev_catalog'], config['prod_catalog']) or drop not in config['drops']:
        raise ValueError('Unknown catalog or drop')
    spark = SparkSession.builder.getOrCreate()
    client = WorkspaceClient()
    def data(path):
        with client.files.download(config['golden_path'] + '/' + path).contents as stream:
            return stream.read()
    manifest = json.loads(data('manifest.json'))
    available = entries(manifest, drop)
    inputs = input_entries(available) if action == 'land' else []
    for schema in ('bronze', 'silver', 'gold', 'verification'):
        spark.sql(f'CREATE SCHEMA IF NOT EXISTS {ident(catalog)}.{ident(schema_name(schema, schema_suffix))}')
    if action == 'land':
        if not inputs:
            raise ValueError(f'No golden inputs for {drop}')
        for entry in inputs:
            rows = read_csv(data(entry['path']), entry)
            schema = StructType([StructField(c, StringType(), True) for c in columns(entry)])
            frame = spark.createDataFrame(rows, schema)
            frame = frame.select(*[F.col(c).cast(column_type(spec, [r[i] for r in rows])).alias(c) for i, (c, spec) in enumerate(zip(columns(entry), entry['columns']))])
            frame = frame.withColumn('_drop', F.lit(drop)).withColumn('_loaded_at', F.current_timestamp())
            table = qualified(catalog, input_table(entry), schema_suffix)
            replace_drop(frame, table, drop)
        return
    units, _, _ = discover(root)
    by_path = {e['path']: e for e in available}
    results = []
    for name, unit in units.items():
        for output in unit['outputs']:
            row = {'run_id': str(run_id), 'drop': drop, 'unit': name, 'table': output['table'],
                   'golden_rows': None, 'actual_rows': None}
            try:
                entry = by_path[output_path(output, drop)]
                expected = read_csv(data(entry['path']), entry)
                projection = ', '.join(ident(c) for c in columns(entry))
                actual = [list(r) for r in spark.sql(f'SELECT {projection} FROM {qualified(catalog, output["table"], schema_suffix)}').collect()]
                row.update(compare(expected, actual, entry))
            except Exception as exc:
                row.update(status='FAIL', detail=str(exc))
            results.append(row)
    schema = 'run_id STRING, drop STRING, unit STRING, table STRING, status STRING, golden_rows BIGINT, actual_rows BIGINT, detail STRING'
    spark.createDataFrame(results, schema).write.format('delta').mode('append').saveAsTable(qualified(catalog, 'verification.parity_results', schema_suffix))
    summary(results)
    if any(r['status'] == 'FAIL' for r in results):
        raise RuntimeError('Strict multiset parity failed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['land', 'parity'])
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--drop', required=True)
    parser.add_argument('--run-id', default='manual')
    parser.add_argument('--schema-suffix', default='')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    run(args.action, root, args.catalog, args.drop, args.run_id, args.schema_suffix)
