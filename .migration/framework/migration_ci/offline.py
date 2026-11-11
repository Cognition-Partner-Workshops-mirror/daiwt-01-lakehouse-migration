import csv
import datetime
import decimal
import json
import os
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import sqlglot
from sqlglot import exp

from .goldens import column_type, columns, needed, output_path, read_csv
from .model import discover, estate, owned_tables, select, statements


def normalize(value, column):
    if value is None or value == r'\N':
        return None
    kind = column.get('type', column.get('redshift_type', column.get('source_type', ''))) if isinstance(column, dict) else ''
    kind = kind.lower()
    text = str(value)
    if any(token in kind for token in ('decimal', 'numeric', 'int', 'float', 'double', 'real')):
        number = decimal.Decimal(text)
        return format(number.normalize(), 'f') if number.is_finite() else str(number)
    if 'bool' in kind:
        if text.lower() in ('true', 't', '1'):
            return 'true'
        if text.lower() in ('false', 'f', '0'):
            return 'false'
        raise ValueError(f'Invalid boolean: {text}')
    if 'timestamp' in kind:
        stamp = datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))
        if stamp.tzinfo:
            stamp = stamp.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return stamp.isoformat(sep=' ')
    if kind == 'date':
        return text[:10]
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, decimal.Decimal):
        return format(value.normalize(), 'f')
    return text


def multiset(rows, entry):
    return Counter(tuple(normalize(value, column) for value, column in zip(row, entry['columns'])) for row in rows)


def compare(expected, actual, entry):
    ok = len(expected) == len(actual) and multiset(expected, entry) == multiset(actual, entry)
    return {'status': 'PASS' if ok else 'FAIL', 'golden_rows': len(expected),
            'actual_rows': len(actual), 'detail': 'matched' if ok else 'row count or content mismatch'}


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def load_table(db, table, entry, rows, drop):
    names = columns(entry)
    defs = ', '.join(f'{quoted(c)} {column_type(spec, (r[i] for r in rows))}'
                     for i, (c, spec) in enumerate(zip(names, entry['columns'])))
    is_input = entry['path'].startswith('inputs/')
    metadata = ', _drop VARCHAR, _loaded_at TIMESTAMP' if is_input else ''
    db.execute(f'CREATE TABLE {table} ({defs}{metadata})')
    if not rows:
        return
    with TemporaryDirectory(prefix='migration-ci-') as directory:
        path = Path(directory) / 'rows.csv'
        max_line_size = 2 * 1024 * 1024
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            for row in rows:
                size = writer.writerow([r'\N' if value is None else value for value in row])
                max_line_size = max(max_line_size, size * 4)
        metadata = ", ?, TIMESTAMP '2000-01-01'" if is_input else ''
        parameters = [drop] if is_input else []
        parameters += [str(path), dict.fromkeys(names, 'VARCHAR'), r'\N', max_line_size]
        db.execute(f'''INSERT INTO {table} SELECT *{metadata} FROM read_csv(
            ?, columns=?, header=false, auto_detect=false, nullstr=?, max_line_size=?,
            delim=',', quote='"', escape='"')''', parameters)


def check(root='.', goldens=None, names=None, changed_from=None, structural=False):
    root = Path(root).resolve()
    config = estate(root)
    units, _, _ = discover(root)
    selected = select(root, units, names, changed_from)
    if structural:
        return [{'unit': n, 'drop': '-', 'status': 'PASS', 'detail': 'structure and SQL validated'} for n in selected]
    cache = Path(goldens) if goldens else root / '.goldens'
    active = [n for n in selected if units[n].get('offline') is not False]
    manifest = json.loads((cache / 'manifest.json').read_text()) if active else {}
    results = []
    for drop in config['drops']:
        required, by_table = needed(manifest, units, active, drop) if active else ({}, {})
        for name in selected:
            unit = units[name]
            if unit.get('offline') is False:
                results.append({'unit': name, 'drop': drop, 'status': 'SKIP', 'detail': unit['reason']})
                continue
            try:
                with duckdb.connect() as db:
                    for layer in ('bronze', 'silver', 'gold'):
                        db.execute(f'CREATE SCHEMA {layer}')
                    own = owned_tables(unit)
                    for table in sorted(unit['references'] - own):
                        entry = by_table[table]
                        rows = read_csv((cache / entry['path']).read_bytes(), entry)
                        load_table(db, table, entry, rows, drop)
                    for file in unit['sql']:
                        for statement in statements((unit['directory'] / file).read_text()):
                            statement = statement.transform(lambda node: exp.Literal.string(drop) if isinstance(node, exp.Placeholder) and node.name == 'drop' else node)
                            if isinstance(statement, exp.Create):
                                statement.set('properties', None)
                            db.execute(statement.sql(dialect='duckdb', unsupported_level=sqlglot.ErrorLevel.RAISE))
                    for output in unit['outputs']:
                        entry = required[output_path(output, drop)]
                        expected = read_csv((cache / entry['path']).read_bytes(), entry)
                        projection = ', '.join(quoted(c) for c in columns(entry))
                        actual = db.execute(f'SELECT {projection} FROM {output["table"]}').fetchall()
                        results.append({'unit': name, 'drop': drop, 'table': output['table'], **compare(expected, actual, entry)})
            except Exception as exc:
                results.append({'unit': name, 'drop': drop, 'status': 'FAIL', 'detail': str(exc)})
    return results


def summary(results):
    def safe(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    text = '| Unit | Drop | Table | Status | Detail |\n|---|---|---|---|---|\n'
    text += ''.join('| ' + ' | '.join(safe(r.get(k, '')) for k in ('unit', 'drop', 'table', 'status', 'detail')) + ' |\n' for r in results)
    if not results:
        text += '| — | — | — | PASS | No selected units |\n'
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(text)
    return text
