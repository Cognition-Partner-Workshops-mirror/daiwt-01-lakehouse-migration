import json
from pathlib import Path

import pytest
import yaml

from bronze_schema import bronze_tables
from generate_silver_asof import functions, generated, self_check

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / 'src/sql/units/silver_asof'
TABLES = bronze_tables(ROOT)


def test_generated_files_are_current():
    for filename, expected in generated().items():
        assert (ROOT / filename).read_text() == expected, 'Run python ci/generate_silver_asof.py'


@pytest.mark.parametrize('table', TABLES)
def test_table_functions_return_exact_source_schema(table):
    sql = (UNIT / 'functions.sql').read_text()
    part = sql.split(f'.`asof_{table}`(as_of_drop STRING)')[1].split('EXECUTE IMMEDIATE')[0]
    columns = TABLES[table]
    assert 'RETURNS TABLE (' + ', '.join(c['name'] + ' ' + c['type'] for c in columns) + ')' in part
    assert 'RETURN SELECT ' + ', '.join(c['name'] for c in columns) in part
    assert '_loaded_at' not in part and '_batch_id' not in part and '_business_date' not in part
    assert '.`resolve_drop`(as_of_drop)' in part
    assert f'PASS asof_{table}' in (UNIT / 'self_check.sql').read_text()


def test_resolver_is_numeric_global_sparse_and_deterministic():
    sql = (UNIT / 'functions.sql').read_text()
    for table in TABLES:
        assert f'.`{table}`' in sql
    assert sql.count('SELECT _drop, _loaded_at FROM') == len(TABLES)
    assert 'try_cast(substring(d, 2) AS INT)' in sql
    assert r"r'^D[0-9]+\z'" in sql
    assert 'WHERE seq <=' in sql and 'max(_loaded_at)' in sql
    assert 'ORDER BY seq DESC, loaded_at DESC, _drop DESC' in sql
    check = (UNIT / 'self_check.sql').read_text()
    for token in ['D10', 'D2', 'D2147483648', 'invalid', 'CAST(NULL AS STRING)', 'EXCEPT ALL', 'raise_error']:
        assert token in check


def test_generator_empty_and_schema_error(tmp_path):
    assert 'asof_' not in functions({})
    assert 'EXCEPT ALL' not in self_check({})
    folder = tmp_path / 'src/sql/units/bronze_landing'
    folder.mkdir(parents=True)
    (folder / 'load.sql').write_text('')
    with pytest.raises(ValueError, match='No bronze'):
        bronze_tables(tmp_path)
    (folder / 'load.sql').write_text("CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :bronze_schema || '.bad') (\n  x UNKNOWN\n) USING DELTA;")
    with pytest.raises(ValueError, match='Unrecognized'):
        bronze_tables(tmp_path)


def test_metadata_job_and_documentation():
    spec = json.loads((UNIT / 'unit.json').read_text())
    assert spec['legacy_unit'] == 'foundation'
    assert spec['depends_on'] == ['bronze_landing']
    assert spec['outputs'] == spec['owns'] == []
    job = yaml.safe_load((UNIT / 'silver_asof.job.yml').read_text())['resources']['jobs']['silver_asof']
    assert job['tasks'][1]['depends_on'] == [{'task_key': 'create_functions'}]
    for task, filename in zip(job['tasks'], ['functions.sql', 'self_check.sql']):
        assert task['sql_task']['file']['path'] == './' + filename
        assert task['sql_task']['parameters']['drop'] == '{{job.parameters.drop}}'
    readme = (UNIT / 'README.md').read_text()
    for text in ['lineage', 'NULL', 'Sparse', 'ci/bronze_schema.py', 'EXCEPT ALL']:
        assert text in readme
