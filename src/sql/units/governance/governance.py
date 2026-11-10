"""Registry-driven Unity Catalog metadata planning and verification."""
import json
from pathlib import Path


def identifier(value):
    return '`' + value.replace('`', '``') + '`'


def literal(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r').replace('\x00', '\\0') + "'"


def load_metadata(root):
    root = Path(root)
    registry = {path.parent.name: json.loads(path.read_text())
                for path in sorted((root / 'src/sql/units').glob('*/unit.json'))}
    return (registry, json.loads((root / 'src/parity/contract.json').read_text()),
            json.loads((root / 'src/sql/units/governance/legacy_metadata.json').read_text()))


def legacy_table(table):
    layer, name = table.split('.')
    if layer == 'bronze':
        for prefix in ('edw', 'stg'):
            if name.startswith(prefix + '_'):
                return 'nvb_' + prefix + '.' + name[len(prefix) + 1:]
    return ('nvb_mart.' if layer == 'gold' else 'nvb_edw.') + name


def build_plan(registry, contract, catalog, schemas, owner, existing, metadata,
               existing_columns=None):
    """Plan registered objects and framework targets, skipping absent objects."""
    targets = {}
    for unit, spec in sorted(registry.items()):
        for table in spec['outputs'] + spec['owns']:
            if table in targets:
                raise ValueError(f'Duplicate governance target: {table}')
            targets[table] = dict(unit=unit, legacy_unit=spec['legacy_unit'],
                                  output=table in spec['outputs'])
    for table, spec in metadata['framework'].items():
        targets.setdefault(table, dict(unit='nvb_nightly', output=False, **spec))
    plans = []
    for table, spec in targets.items():
        layer, name = table.split('.')
        if layer not in ('bronze', 'silver', 'gold') or schemas[layer] == 'landing':
            raise ValueError('landing is not a governance target')
        schema = schemas[layer]
        entries = [entry for entry in contract['outputs'] if entry['table'] == table]
        if spec['output'] and not entries:
            raise ValueError(f'No contract for {table}')
        source = spec.get('legacy_table', legacy_table(table))
        legacy = metadata['tables'].get(source, {})
        comment = (legacy.get('comment') or spec.get('comment') or f'Teradata source: {source}.')
        comment += f" {layer.title()} object; producing unit: {spec['unit']}."
        if entries:
            comment += ' Parity contract: ' + ', '.join(sorted({entry['path'] for entry in entries})) + '.'
        business_owner = metadata['business_owners'].get(spec['legacy_unit'],
                                                        metadata['business_owners']['upstream'])
        tags = dict(source_system='teradata', medallion_layer=layer,
                    migration_unit=spec['unit'], legacy_unit=spec['legacy_unit'],
                    parity_contract='yes' if entries else 'no', business_owner=business_owner)
        columns = dict(legacy.get('columns', {}))
        if entries:
            definitions = {}
            for entry in entries:
                for column in entry['columns']:
                    key, value = column['name'], column['source_type']
                    if key in definitions and definitions[key] != value:
                        raise ValueError(f'Conflicting column contract for {table}')
                    definitions[key] = value
            columns = {key: (columns.get(key, '') + f' Legacy Teradata type: {value}').strip()
                       for key, value in definitions.items()}
        elif existing_columns is not None:
            columns = {key: value for key, value in columns.items()
                       if key in existing_columns.get((schema, name), {})}
        row = existing.get((schema, name))
        plan = dict(table=table, schema=schema, name=name, unit=spec['unit'],
                    comment=comment, tags=tags, columns=columns, owner=owner,
                    pipeline=False, status='APPLY' if row else 'SKIP', statements=[])
        if row:
            forms = {'MANAGED': 'TABLE', 'EXTERNAL': 'TABLE', 'VIEW': 'VIEW',
                     'MATERIALIZED_VIEW': 'MATERIALIZED VIEW', 'STREAMING_TABLE': 'STREAMING TABLE'}
            form = forms[row['table_type']]
            target = '.'.join(map(identifier, (catalog, schema, name)))
            plan['pipeline'] = row['table_type'] in ('MATERIALIZED_VIEW', 'STREAMING_TABLE')
            statements = [('comment', f'COMMENT ON {form} {target} IS {literal(comment)}')]
            statements += [('column:' + column, f'COMMENT ON COLUMN {target}.{identifier(column)} IS {literal(value)}')
                           for column, value in columns.items()]
            statements += [('tags', f'ALTER {form} {target} SET TAGS (' + ', '.join(
                f'{literal(key)} = {literal(value)}' for key, value in tags.items()) + ')')]
            if owner:
                statements += [('owner', f'ALTER {form} {target} OWNER TO {identifier(owner)}')]
            plan['statements'] = statements
        plans.append(plan)
    return plans


def apply_plan(spark, plan):
    exemptions = []
    for field, statement in plan['statements']:
        try:
            spark.sql(statement).collect()
        except Exception as error:
            condition = error.getCondition() if hasattr(error, 'getCondition') else ''
            if (plan['pipeline'] and field != 'tags' and condition
                    and ('NOT_SUPPORTED' in condition or 'UNSUPPORTED' in condition)):
                exemptions.append(field)
                print(f"NOTE {plan['table']} {field}: {error}")
            else:
                raise
    return exemptions


def read_state(spark, catalog, schemas):
    prefix = identifier(catalog) + '.information_schema.'
    predicate = 'table_schema IN (' + ', '.join(literal(schema) for schema in schemas.values()) + ')'
    tables = {(row['table_schema'], row['table_name']): row.asDict()
              for row in spark.sql(f'SELECT * FROM {prefix}tables WHERE {predicate}').collect()}
    tags = {}
    tag_predicate = predicate.replace('table_schema', 'schema_name')
    for row in spark.sql(f'SELECT * FROM {prefix}table_tags WHERE {tag_predicate}').collect():
        tags.setdefault((row['schema_name'], row['table_name']), {})[row['tag_name']] = row['tag_value']
    columns = {}
    for row in spark.sql(f'SELECT * FROM {prefix}columns WHERE {predicate}').collect():
        columns.setdefault((row['table_schema'], row['table_name']), {})[row['column_name']] = row['comment']
    return tables, tags, columns


def mismatches(plan, tables, tags, columns, exemptions=()):
    key = (plan['schema'], plan['name'])
    row = tables[key]
    expected = {'comment': (row['comment'], plan['comment'])}
    if plan['owner']:
        expected['owner'] = (row['table_owner'], plan['owner'])
    expected.update({'column:' + name: (columns.get(key, {}).get(name), value)
                     for name, value in plan['columns'].items()})
    failures = [field for field, (actual, value) in expected.items()
                if actual != value and field not in exemptions]
    failures += ['tag:' + name for name, value in plan['tags'].items()
                 if tags.get(key, {}).get(name) != value]
    return failures


def summary(plans):
    return '| table | unit | status |\n| --- | --- | --- |\n' + '\n'.join(
        f"| {plan['table']} | {plan['unit']} | {plan['status']} |" for plan in plans)
