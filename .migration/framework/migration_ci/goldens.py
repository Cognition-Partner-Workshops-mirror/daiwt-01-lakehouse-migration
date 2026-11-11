import csv
import decimal
import hashlib
import io
import json
import re
from pathlib import Path

from .model import owned_tables, relative


def entries(manifest, drop):
    result = []
    for entry in manifest.get('files', manifest.get('tables', [])):
        parts = relative(entry['path']).parts
        if len(parts) < 3 or parts[0] not in ('inputs', 'outputs'):
            continue
        if parts[1] == drop:
            result.append(entry)
    paths = [e['path'] for e in result]
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate manifest path')
    return result


def columns(entry):
    return [c['name'] if isinstance(c, dict) else c for c in entry['columns']]


def column_type(column, values=()):
    kind = column.get('type', column.get('redshift_type', column.get('source_type', 'string'))) if isinstance(column, dict) else 'string'
    kind = kind.lower()
    aliases = {'int8': 'bigint', 'int4': 'int', 'int2': 'smallint', 'bool': 'boolean',
               'float8': 'double', 'float4': 'float', 'bpchar': 'string', 'text': 'string', 'super': 'string'}
    kind = aliases.get(kind, kind)
    if kind in ('numeric', 'decimal'):
        numbers = [decimal.Decimal(v) for v in values if v is not None]
        if not numbers:
            return 'decimal(38,18)'
        if any(not n.is_finite() for n in numbers):
            raise ValueError('Non-finite decimal in golden')
        scale = max(max(0, -n.as_tuple().exponent) for n in numbers)
        integral = max(max(1, n.adjusted() + 1) for n in numbers)
        if integral + scale > 38:
            raise ValueError('Golden decimal exceeds precision 38')
        return f'decimal({integral + scale},{scale})'
    if kind.startswith(('varchar', 'char', 'nvarchar', 'nchar')):
        return 'string'
    if not re.fullmatch(r'(string|bigint|int|smallint|boolean|double|float|date|timestamp|decimal\(\d+,\s*\d+\)|numeric\(\d+,\s*\d+\))', kind):
        raise ValueError(f'Unsupported golden column type: {kind}')
    return kind


def output_path(output, drop):
    parts = list(relative(output['golden']).parts)
    parts[1] = drop
    return '/'.join(parts)


def input_table(entry):
    source = entry.get('source_table', Path(entry['path']).stem)
    return 'bronze.' + source.split('.')[-1]


def input_entries(available):
    inputs = [e for e in available if e['path'].startswith('inputs/')]
    tables = {}
    for entry in inputs:
        table = input_table(entry)
        if table in tables:
            raise ValueError(f'Multiple golden inputs map to {table}: {tables[table]}, {entry["path"]}')
        tables[table] = entry['path']
    return inputs


def needed(manifest, units, selected, drop):
    available = entries(manifest, drop)
    by_path = {e['path']: e for e in available}
    by_table = {input_table(e): e for e in input_entries(available)}
    for entry in available:
        if entry['path'].startswith('outputs/') and entry.get('table'):
            table = entry['table']
            if table in by_table:
                raise ValueError(f'Multiple goldens map to {table} in {drop}')
            by_table[table] = entry
    for unit in units.values():
        for output in unit['outputs']:
            path = output_path(output, drop)
            if path in by_path:
                by_table[output['table']] = by_path[path]
    required = {}
    for name in selected:
        unit = units[name]
        if unit.get('offline') is False:
            continue
        own = owned_tables(unit)
        for table in unit['references'] - own:
            if table not in by_table:
                raise ValueError(f'No golden for referenced table {table} in {drop}')
            required[by_table[table]['path']] = by_table[table]
        for output in unit['outputs']:
            path = output_path(output, drop)
            if path not in by_path:
                raise ValueError(f'Missing output golden in manifest: {path}')
            required[path] = by_path[path]
    return required, by_table


def read_csv(data, entry):
    if entry.get('sha256') and hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise ValueError(f"Golden checksum mismatch: {entry['path']}")
    reader = csv.reader(io.StringIO(data.decode('utf-8')))
    if next(reader, None) != columns(entry):
        raise ValueError(f"Golden header mismatch: {entry['path']}")
    rows = [[None if cell == r'\N' else cell for cell in row] for row in reader]
    if any(len(row) != len(columns(entry)) for row in rows):
        raise ValueError('CSV row width differs from manifest')
    if 'row_count' in entry and len(rows) != entry['row_count']:
        raise ValueError('CSV row count differs from manifest')
    return rows


def fetch(root, config, units, selected, client=None):
    if client is None:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient()
    cache = Path(root) / '.goldens'
    cache.mkdir(exist_ok=True)
    def download(path):
        with client.files.download(config['golden_path'] + '/' + path).contents as stream:
            return stream.read()
    manifest_data = download('manifest.json')
    manifest = json.loads(manifest_data)
    required = {}
    for drop in config['drops']:
        required.update(needed(manifest, units, selected, drop)[0])
    for path, entry in required.items():
        target = cache / relative(path)
        if target.exists() and entry.get('sha256') == hashlib.sha256(target.read_bytes()).hexdigest():
            continue
        data = download(path)
        read_csv(data, entry)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (cache / 'manifest.json').write_bytes(manifest_data)
    return len(required)
