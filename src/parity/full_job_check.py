"""Fail-closed full D0 output and reference-control verification."""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from parity_check import GOLDEN_PATH, ROOT, GoldenStore, Warehouse, identifier, run as parity_run

sys.path.insert(0, str(ROOT / 'ci'))
from registry import load, validate


def require_complete_registry(registry, contract):
    validate(registry, contract)
    required = {entry['table'] for entry in contract['outputs'] if entry['drop'] == 'D0'}
    produced = {table for spec in registry.values() for table in spec['outputs']}
    missing = required - produced
    if not required or missing:
        raise ValueError('Full D0 registry incomplete: ' + ', '.join(sorted(missing)))
    if not any(spec['job'] == 'governance' for spec in registry.values()):
        raise ValueError('Full job requires governance')


def compare_controls(manifest, download, warehouse, suffix):
    prefix = 'controls/D0/'
    files = {entry['path']: entry for entry in manifest['files'] if entry['path'].startswith(prefix)}
    if any('..' in path.split('/') for path in files):
        raise ValueError('Invalid control path')
    expected_paths = {path for path in files if path.startswith(prefix + 'expected/') and path.endswith('.json')}
    if not expected_paths or set(files) != expected_paths | {prefix + 'summary.csv'}:
        raise ValueError('Missing or unexpected D0 control files')
    outputs = {entry['source_table']: entry['table'] for entry in manifest['files']
               if entry['path'].startswith('outputs/D0/')}
    inputs = {entry['table']: 'bronze.' + entry['table'].replace('nvb_', '').replace('.', '_')
              for entry in manifest['files'] if entry['path'].startswith('inputs/D0/') and entry.get('format') == 'csv'}
    targets = {**inputs, **outputs}
    excluded = {table: {entry['column'] for entry in manifest.get('excluded_from_parity', [])
                        if table in entry['tables']} for table in targets}
    counts = {}

    def target(table, mapping=targets):
        layer, name = mapping[table].split('.')
        return '.'.join(identifier(part) for part in ('daiwt_01_mig_dev', layer + '_' + suffix, name))

    def count(table, mapping=targets):
        key = mapping[table]
        if key not in counts:
            counts[key] = int(warehouse.sql('SELECT count(*) FROM ' + target(table, mapping))[0][0])
        return counts[key]

    def read(path):
        data = download(path)
        if hashlib.sha256(data).hexdigest() != files[path]['sha256']:
            raise ValueError('Control checksum mismatch: ' + path)
        return data.decode('utf-8')

    results, baselines = [], {}
    for path in sorted(expected_paths):
        try:
            expected = json.loads(read(path), parse_float=Decimal)
            table = expected['table']
            if path != prefix + 'expected/' + table + '.json' or table in baselines:
                raise ValueError('Control table/path mismatch')
            baselines[table] = expected['rows']
            if count(table) != expected['rows']:
                raise ValueError('Control row count mismatch')
            if set(expected['input_rows']) != set(inputs):
                raise ValueError('Control input coverage mismatch')
            if any(count(name, inputs) != rows for name, rows in expected['input_rows'].items()):
                raise ValueError('Control input count mismatch')
            for metric, values in expected.items():
                if metric in ('table', 'rows', 'input_rows'):
                    continue
                if metric in ('distinct', 'sums', 'dates'):
                    for column, value in values.items():
                        if column in excluded[table]:
                            continue
                        col = identifier(column)
                        if metric == 'dates':
                            actual = warehouse.sql(f'SELECT CAST(min({col}) AS STRING), CAST(max({col}) AS STRING) FROM {target(table)}')[0]
                            matches = actual == [value['min'], value['max']]
                        else:
                            expression = f'count(DISTINCT {col})' if metric == 'distinct' else f'round(coalesce(sum({col}), 0), 2)'
                            actual = warehouse.sql(f'SELECT {expression} FROM {target(table)}')[0][0]
                            matches = actual is not None and Decimal(str(actual)) == Decimal(str(value))
                        if not matches:
                            raise ValueError(f'Control mismatch: {metric}.{column}')
                elif metric.endswith('_counts'):
                    column = metric[:-len('_counts')]
                    if column in excluded[table]:
                        continue
                    col = identifier(column)
                    rows = warehouse.sql(f'SELECT CAST({col} AS STRING), count(*) FROM {target(table)} WHERE {col} IS NOT NULL GROUP BY {col}')
                    if {key: int(value) for key, value in rows} != values:
                        raise ValueError('Control mismatch: ' + metric)
                else:
                    raise ValueError('Unknown control metric: ' + metric)
            results.append(dict(table=path, drop='D0', status='PASS', reason='matched reference metrics'))
        except Exception as error:
            results.append(dict(table=path, drop='D0', status='FAIL', reason=str(error)))
    path = prefix + 'summary.csv'
    try:
        reader = csv.DictReader(io.StringIO(read(path)))
        if reader.fieldnames != ['table', 'rows']:
            raise ValueError('Invalid control summary columns')
        rows = list(reader)
        summary = {row['table']: int(row['rows']) for row in rows}
        if len(summary) != len(rows) or summary != baselines or set(summary) != set(targets):
            raise ValueError('Control summary coverage mismatch')
        if any(count(table) != rows for table, rows in summary.items()):
            raise ValueError('Control summary row count mismatch')
        results.append(dict(table=path, drop='D0', status='PASS', reason='matched summary'))
    except Exception as error:
        results.append(dict(table=path, drop='D0', status='FAIL', reason=str(error)))
    return results


def run(client, suffix, warehouse_id, registry, contract):
    if not re.fullmatch(r'pr_[0-9]+', suffix):
        raise ValueError('Full verification requires a dev PR suffix')
    require_complete_registry(registry, contract)
    result = parity_run(client, 'daiwt_01_mig_dev', suffix, warehouse_id, registry, 'D0', contract=contract)
    if any(row['status'] != 'PASS' for row in result['results']) or not result['results']:
        raise ValueError('Full verification requires every contract output, with no SKIP')
    store = GoldenStore(client, GOLDEN_PATH)

    def download(path):
        with client.files.download(GOLDEN_PATH + '/' + path).contents as stream:
            return stream.read()

    result['results'] += compare_controls(store.manifest(), download, Warehouse(client, warehouse_id), suffix)
    result['counts'] = {status: sum(row['status'] == status for row in result['results'])
                        for status in ('PASS', 'FAIL', 'SKIP')}
    result.update(status='FAIL' if result['counts']['FAIL'] else 'PASS',
                  goldens=len(result['results']), compared=len(result['results']))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suffix', required=True)
    parser.add_argument('--warehouse-id', default='6dc4b80538621b7b')
    parser.add_argument('--output', default='out/parity-D0.json')
    args = parser.parse_args()
    try:
        from databricks.sdk import WorkspaceClient
        result = run(WorkspaceClient(), args.suffix, args.warehouse_id, load(ROOT),
                     json.loads((ROOT / 'src/parity/contract.json').read_text()))
    except Exception as error:
        result = dict(status='FAIL', drop='D0', error=str(error), results=[], goldens=0,
                      compared=0, counts=dict(PASS=0, FAIL=0, SKIP=0))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(f"{result['status']} full D0: {result['counts']}")
    if result.get('error'):
        print(result['error'])
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
