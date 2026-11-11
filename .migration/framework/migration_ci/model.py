import re
import subprocess
from graphlib import TopologicalSorter
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp
from sqlglot.tokens import TokenType

NAME = re.compile(r"^[a-z][a-z0-9_]*$")
TABLE = re.compile(r"^(bronze|silver|gold)\.[a-z][a-z0-9_]*$")
IDENTIFIER = re.compile(
    r"IDENTIFIER\(\s*:catalog\s*\|\|\s*'\.'\s*\|\|\s*:(bronze|silver|gold)_schema\s*\|\|\s*'\.([a-z][a-z0-9_]*)'\s*\)", re.I
)


def schema_name(layer, suffix=''):
    if suffix and not re.fullmatch(r'_[a-z][a-z0-9_]*', suffix):
        raise ValueError('schema_suffix must be empty or an underscore followed by lowercase letters/digits/underscores')
    return layer + suffix


def relative(path):
    path = Path(path)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f"Unsafe relative path: {path}")
    return path


def estate(root):
    data = yaml.safe_load((Path(root) / 'estate.yml').read_text())
    required = {'estate', 'source_dialect', 'dev_catalog', 'prod_catalog', 'warehouse_id',
                'golden_path', 'drops', 'legacy_repo'}
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError(f"estate.yml requires exactly {sorted(required)}")
    for key in ('estate', 'dev_catalog', 'prod_catalog'):
        if not isinstance(data[key], str) or not NAME.fullmatch(data[key]):
            raise ValueError(f"Invalid {key}")
    if data['source_dialect'] not in ('tsql', 'redshift', 'teradata'):
        raise ValueError('Invalid source_dialect')
    if not isinstance(data['drops'], list) or not data['drops'] or any(
            not isinstance(d, str) or not re.fullmatch(r'D[0-9]+', d) for d in data['drops']):
        raise ValueError('drops must be a non-empty list of D0..Dn')
    if len(set(data['drops'])) != len(data['drops']):
        raise ValueError('Duplicate drops')
    for key in ('warehouse_id', 'golden_path', 'legacy_repo'):
        if not isinstance(data[key], str) or not data[key]:
            raise ValueError(f'Invalid {key}')
    if not re.fullmatch(r'/Volumes/[^/]+/[^/]+/[^/]+', data['golden_path']):
        raise ValueError('golden_path must name a UC volume')
    return data


def rewrite(sql):
    sql = IDENTIFIER.sub(lambda m: f'{m[1].lower()}.{m[2]}', sql)
    if re.search(r'\bIDENTIFIER\s*\(', sql, re.I):
        raise ValueError('Unsupported IDENTIFIER expression; use the documented catalog/layer convention')
    return sql


def statements(sql):
    sql = rewrite(sql)
    tokens = sqlglot.tokenize(sql, read='databricks')
    for i in range(len(tokens) - 1, 0, -1):
        if tokens[i - 1].token_type == TokenType.COLON and tokens[i].text.lower() == 'drop':
            token = tokens[i]
            sql = sql[:token.start] + 'migration_ci_drop' + sql[token.end + 1:]
    result = sqlglot.parse(sql, read='databricks', error_level=sqlglot.ErrorLevel.RAISE)
    if not result or any(s is None or isinstance(s, exp.Command) for s in result):
        raise ValueError('SQL must contain parseable Databricks statements')
    return [s.transform(lambda n: exp.Placeholder(this='drop') if isinstance(n, exp.Placeholder) and n.name == 'migration_ci_drop' else n) for s in result]


def references(sql):
    refs = set()
    for statement in statements(sql):
        for table in statement.find_all(exp.Table):
            if table.db:
                refs.add(f'{table.db}.{table.name}'.lower())
    return refs


def created_tables(sql):
    tables = set()
    for statement in statements(sql):
        if isinstance(statement, exp.Create) and statement.kind in ('TABLE', 'VIEW'):
            target = statement.this
            if isinstance(target, exp.Schema):
                target = target.this
            if isinstance(target, exp.Table) and target.db:
                tables.add(f'{target.db}.{target.name}'.lower())
    return tables


def owned_tables(unit):
    return {o['table'] for o in unit['outputs']} | unit.get('created_tables', set())


def discover(root):
    root = Path(root)
    units, owners = {}, {}
    for directory in (root / 'src/units').glob('*'):
        if directory.is_dir() and not directory.name.startswith('.') and not (directory / 'unit.yml').is_file():
            raise ValueError(f'Missing unit.yml: {directory}')
    for file in sorted((root / 'src/units').glob('*/unit.yml')):
        spec = yaml.safe_load(file.read_text())
        required = {'name', 'layer', 'sql', 'outputs'}
        if not isinstance(spec, dict) or not required <= spec.keys() or set(spec) - required - {'offline', 'reason'}:
            raise ValueError(f'Invalid unit schema: {file}')
        name = spec['name']
        if not isinstance(name, str) or not NAME.fullmatch(name) or file.parent.name != name:
            raise ValueError(f'Unit name must match directory: {file}')
        if spec['layer'] not in ('bronze', 'silver', 'gold'):
            raise ValueError(f'Invalid layer: {name}')
        if not isinstance(spec.get('offline', True), bool):
            raise ValueError(f'offline must be boolean: {name}')
        if spec.get('offline') is False and (not isinstance(spec.get('reason'), str) or not spec['reason'].strip()):
            raise ValueError(f'offline: false requires reason: {name}')
        if not isinstance(spec['sql'], list) or not spec['sql'] or len(set(spec['sql'])) != len(spec['sql']):
            raise ValueError(f'sql must be a non-empty ordered unique list: {name}')
        refs, created = set(), set()
        for sql in spec['sql']:
            path = file.parent / relative(sql)
            if path.suffix != '.sql':
                raise ValueError(f'Not SQL: {path}')
            refs |= references(path.read_text())
            created |= created_tables(path.read_text())
        if not isinstance(spec['outputs'], list) or not spec['outputs']:
            raise ValueError(f'outputs must be non-empty: {name}')
        for output in spec['outputs']:
            if not isinstance(output, dict) or set(output) != {'table', 'golden', 'keys'}:
                raise ValueError(f'Invalid output: {name}')
            table = output['table']
            if not isinstance(table, str) or not TABLE.fullmatch(table) or table.split('.')[0] != spec['layer']:
                raise ValueError(f'Invalid output table: {table}')
            if table in owners:
                raise ValueError(f'Multiple owners for {table}: {owners[table]}, {name}')
            owners[table] = name
            golden = relative(output['golden'])
            if len(golden.parts) < 3 or golden.parts[0] != 'outputs' or not re.fullmatch(r'D[0-9]+', golden.parts[1]):
                raise ValueError(f'Golden must be under outputs/<drop>/: {golden}')
            if not isinstance(output['keys'], list) or any(not isinstance(k, str) or not NAME.fullmatch(k) for k in output['keys']):
                raise ValueError(f'Invalid keys: {name}')
        units[name] = {**spec, 'directory': file.parent, 'references': refs, 'created_tables': created}
    for name, unit in units.items():
        for table in unit['created_tables']:
            if table in owners and owners[table] != name:
                raise ValueError(f'Multiple owners for {table}: {owners[table]}, {name}')
            owners[table] = name
    graph = {name: {owners[t] for t in u['references'] if t in owners and owners[t] != name}
             for name, u in units.items()}
    tuple(TopologicalSorter(graph).static_order())
    return units, owners, graph


def select(root, units, names=None, changed_from=None):
    selected = set(units) if names is None else set(names)
    if selected - units.keys():
        raise ValueError(f'Unknown units: {sorted(selected - units.keys())}')
    if changed_from:
        paths = subprocess.check_output(['git', 'diff', '--name-only', f'{changed_from}...HEAD'], cwd=root, text=True).splitlines()
        global_change = any(not p.startswith(('src/units/', 'README', 'docs/')) for p in paths)
        changed = {p.split('/')[2] for p in paths if p.startswith('src/units/') and len(p.split('/')) > 3}
        if not global_change:
            selected &= changed
    return sorted(selected)
