import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from bronze_schema import bronze_tables
from generate_silver_asof import manifest_drops

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / 'src/sql/units/bronze_landing'
LOAD = (UNIT / 'load.sql').read_text()
CHECK = (UNIT / 'self_check.sql').read_text()
MANIFEST = json.loads((ROOT / 'golden/manifest.json').read_text())
TABLES = bronze_tables(ROOT)
SOURCE = {
    'nvb_edw.dim_account': dict(name='edw_dim_account', keys=["account_id"], columns=TABLES['edw_dim_account']),
    'nvb_edw.dim_branch': dict(name='edw_dim_branch', keys=["branch_id"], columns=TABLES['edw_dim_branch']),
    'nvb_edw.dim_calendar': dict(name='edw_dim_calendar', keys=["calendar_date"], columns=TABLES['edw_dim_calendar']),
    'nvb_edw.dim_card': dict(name='edw_dim_card', keys=["card_id"], columns=TABLES['edw_dim_card']),
    'nvb_edw.dim_merchant': dict(name='edw_dim_merchant', keys=["merchant_id"], columns=TABLES['edw_dim_merchant']),
    'nvb_edw.dim_product': dict(name='edw_dim_product', keys=["product_id"], columns=TABLES['edw_dim_product']),
    'nvb_edw.fct_daily_balance': dict(name='edw_fct_daily_balance', keys=["account_id","balance_date"], columns=TABLES['edw_fct_daily_balance']),
    'nvb_edw.fct_loan': dict(name='edw_fct_loan', keys=["loan_id"], columns=TABLES['edw_fct_loan']),
    'nvb_edw.fct_product_order': dict(name='edw_fct_product_order', keys=["order_id"], columns=TABLES['edw_fct_product_order']),
    'nvb_stg.customer_feed': dict(name='stg_customer_feed', keys=["customer_id"], columns=TABLES['stg_customer_feed']),
    'nvb_stg.transaction_feed': dict(name='stg_transaction_feed', keys=["transaction_id"], columns=TABLES['stg_transaction_feed']),
}


def test_all_csv_inputs_and_no_outputs():
    entries = [e for e in MANIFEST['files'] if e.get('format') == 'csv' and e['path'].startswith('inputs/')]
    assert {e['table'] for e in entries} == set(SOURCE)
    assert LOAD.count('FROM read_files(') == len({e['table'] for e in entries})
    for entry in entries:
        spec = SOURCE[entry['table']]
        assert entry['columns'] == [c['name'] for c in spec['columns']]
        assert f"/{entry['table']}/{entry['table']}.csv" in LOAD
        assert f"element_at(_landing_counts, '{entry['table']}')" in CHECK
    assert '/outputs/' not in LOAD and '.parquet' not in LOAD


@pytest.mark.parametrize('source', SOURCE)
def test_typed_ddl_and_explicit_projection(source):
    spec = SOURCE[source]
    target = "IDENTIFIER(:catalog || '.' || :bronze_schema || '." + spec['name'] + "')"
    create = LOAD.split('CREATE TABLE IF NOT EXISTS ' + target + ' (')[1].split(') USING DELTA;')[0]
    for column in spec['columns']:
        assert column['name'] + ' ' + column['type'] + (' NOT NULL' if column['required'] else '') in create
        if column['type'] == 'STRING':
            expression = f"COALESCE({column['name']}, '') AS {column['name']}"
        else:
            expression = f"CAST(NULLIF({column['name']}, '') AS {column['type']}) AS {column['name']}"
        assert expression in LOAD
    assert f'DELETE FROM {target} WHERE _drop = :drop;' in LOAD
    assert 'INSERT INTO ' + target in LOAD
    for key in spec['keys']:
        assert key + ' IS NULL' in CHECK
    assert spec['name'] + ': row count, keys or lineage failed' in CHECK


def test_lineage_and_fail_closed_reader():
    for token in ["SET ANSI_MODE = true", "sep => '|'", "mode => 'FAILFAST'", 'enforceSchema => false',
                  "schemaEvolutionMode => 'rescue'", "rescuedDataColumn => '_rescued_data'",
                  'readerCaseSensitive => true', '_rescued_data IS NULL', '_metadata.file_path',
                  'current_timestamp()', "Unknown captured drop", '_landing_business_date', '_landing_batch_id']:
        assert token in LOAD
    for column in ['_drop', '_batch_id', '_business_date', '_source_file', '_loaded_at']:
        assert column + ' IS NULL' in CHECK
    assert 'TRY_CAST' not in LOAD and 'SELECT *' not in LOAD
    assert "r'\\\\N'" not in LOAD


def test_registry_and_task_chain():
    spec = json.loads((UNIT / 'unit.json').read_text())
    assert spec['outputs'] == [] and spec['depends_on'] == []
    assert set(spec['owns']) == {'bronze.' + s['name'] for s in SOURCE.values()}
    job = yaml.safe_load((UNIT / 'bronze_landing.job.yml').read_text())['resources']['jobs']['bronze_landing']
    assert job['name'] == '${var.job_name_prefix}bronze_landing'
    assert job['parameters'][0] == {'name': 'drop', 'default': 'D0'}
    assert json.loads(job['parameters'][1]['default']) == manifest_drops(MANIFEST)
    assert job['tasks'][1]['depends_on'] == [{'task_key': 'load_inputs'}]
    for task, filename in zip(job['tasks'], ['load.sql', 'self_check.sql']):
        assert task['sql_task']['file'] == {'path': './' + filename, 'source': 'WORKSPACE'}
        assert task['sql_task']['parameters']['drop'] == '{{job.parameters.drop}}'
        assert task['sql_task']['warehouse_id'] == '${var.warehouse_id}'
    readme = (UNIT / 'README.md').read_text()
    assert all(t in readme and s['name'] in readme for t, s in SOURCE.items())


def test_metadata_is_not_pinned_in_sql():
    for sql in [LOAD, CHECK]:
        assert "CASE :drop" not in sql and "'D0'" not in sql
        assert 'from_json(:manifest_drops' in sql
        assert 'Unknown captured drop' in sql
    assert LOAD.index('Unknown captured drop') < LOAD.index('DELETE FROM')


def test_generated_metadata_supports_new_drop():
    manifest = copy.deepcopy(MANIFEST)
    manifest['drops'].append(dict(name='D2', batch_id=20250702, business_date='2025-07-02'))
    for entry in list(manifest['files']):
        if entry['path'].startswith('inputs/D0/'):
            manifest['files'].append({**entry, 'path': entry['path'].replace('inputs/D0/', 'inputs/D2/')})
    metadata = manifest_drops(manifest)
    assert metadata['D2']['batch_id'] == 20250702
    assert metadata['D2']['counts'] == metadata['D0']['counts']
    assert metadata['D2']['business_date'] == '2025-07-02'
    assert 'unknown' not in metadata
    with pytest.raises(ValueError, match='Incomplete'):
        manifest_drops({'drops': [{'name': 'D9'}], 'files': []})
    with pytest.raises(ValueError, match='Duplicate'):
        manifest_drops({**MANIFEST, 'drops': MANIFEST['drops'] * 2})
