"""Read ordered source columns from the bronze CREATE TABLE definitions."""
import re
from pathlib import Path


def bronze_tables(root):
    sql = (Path(root) / 'src/sql/units/bronze_landing/load.sql').read_text()
    tables = {}
    for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS IDENTIFIER\([^\n]+'\.([a-z_]+)'\) \((.*?)\n\) USING DELTA;", sql, re.S):
        columns = []
        for line in body.splitlines():
            match = re.fullmatch(r'\s*([a-z_]+) (INT|BIGINT|STRING|DATE|TIMESTAMP_NTZ|TIMESTAMP|DECIMAL\(\d+,\d+\))( NOT NULL)?,?', line)
            if not line.strip():
                continue
            if not match:
                raise ValueError(f'Unrecognized bronze column: {line}')
            column, kind, required = match.groups()
            if not column.startswith('_'):
                columns.append(dict(name=column, type=kind, required=bool(required)))
        tables[name] = columns
    if not tables:
        raise ValueError('No bronze table definitions found')
    return tables
