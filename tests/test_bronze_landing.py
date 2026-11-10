import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / 'src/sql/units/bronze_landing'
LOAD = (UNIT / 'load.sql').read_text()
CHECK = (UNIT / 'self_check.sql').read_text()
MANIFEST = json.loads((ROOT / 'golden/manifest.json').read_text())
SOURCE = {
    'nvb_edw.dim_account': dict(name='edw_dim_account', keys=["account_id"], columns=[
        dict(name='account_id', source='INTEGER', type='INT', required=False),
        dict(name='customer_id', source='INTEGER', type='INT', required=False),
        dict(name='product_id', source='INTEGER', type='INT', required=False),
        dict(name='opened_date', source='DATE', type='DATE', required=False),
        dict(name='dormant_flag', source='CHAR(1)', type='STRING', required=False),
        dict(name='batch_id', source='BIGINT', type='BIGINT', required=True),
        dict(name='load_ts', source='TIMESTAMP(0)', type='TIMESTAMP_NTZ', required=True),
        dict(name='src_system_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='record_status_cd', source='CHAR(1)', type='STRING', required=False),
        dict(name='effective_date', source='DATE', type='DATE', required=False),
        dict(name='expiry_date', source='DATE', type='DATE', required=False),
        dict(name='currency_cd', source='CHAR(3)', type='STRING', required=False),
        dict(name='account_status_cd', source='VARCHAR(10)', type='STRING', required=False),
        dict(name='accrual_basis_cd', source='VARCHAR(8)', type='STRING', required=False),
        dict(name='overdraft_limit', source='DECIMAL(18,2)', type='DECIMAL(18,2)', required=False),
    ]),
    'nvb_edw.dim_branch': dict(name='edw_dim_branch', keys=["branch_id"], columns=[
        dict(name='branch_id', source='INTEGER', type='INT', required=False),
        dict(name='town', source='VARCHAR(30)', type='STRING', required=False),
        dict(name='batch_id', source='BIGINT', type='BIGINT', required=True),
        dict(name='load_ts', source='TIMESTAMP(0)', type='TIMESTAMP_NTZ', required=True),
        dict(name='src_system_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='record_status_cd', source='CHAR(1)', type='STRING', required=False),
        dict(name='effective_date', source='DATE', type='DATE', required=False),
        dict(name='expiry_date', source='DATE', type='DATE', required=False),
        dict(name='branch_name', source='VARCHAR(80)', type='STRING', required=False),
        dict(name='region_cd', source='VARCHAR(8)', type='STRING', required=False),
        dict(name='branch_type_cd', source='VARCHAR(10)', type='STRING', required=False),
        dict(name='service_language_cd', source='CHAR(2)', type='STRING', required=False),
        dict(name='branch_status_cd', source='VARCHAR(10)', type='STRING', required=False),
    ]),
    'nvb_edw.dim_calendar': dict(name='edw_dim_calendar', keys=["calendar_date"], columns=[
        dict(name='calendar_date', source='DATE', type='DATE', required=False),
        dict(name='month_start', source='DATE', type='DATE', required=False),
        dict(name='year_no', source='INTEGER', type='INT', required=False),
        dict(name='month_no', source='INTEGER', type='INT', required=False),
        dict(name='batch_id', source='BIGINT', type='BIGINT', required=True),
        dict(name='load_ts', source='TIMESTAMP(0)', type='TIMESTAMP_NTZ', required=True),
        dict(name='src_system_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='record_status_cd', source='CHAR(1)', type='STRING', required=False),
        dict(name='effective_date', source='DATE', type='DATE', required=False),
        dict(name='expiry_date', source='DATE', type='DATE', required=False),
        dict(name='calendar_name', source='VARCHAR(20)', type='STRING', required=False),
        dict(name='business_day_rule_cd', source='VARCHAR(12)', type='STRING', required=False),
    ]),
    'nvb_edw.dim_card': dict(name='edw_dim_card', keys=["card_id"], columns=[
        dict(name='card_id', source='INTEGER', type='INT', required=False),
        dict(name='account_id', source='INTEGER', type='INT', required=False),
        dict(name='card_type', source='VARCHAR(10)', type='STRING', required=False),
        dict(name='batch_id', source='BIGINT', type='BIGINT', required=True),
        dict(name='load_ts', source='TIMESTAMP(0)', type='TIMESTAMP_NTZ', required=True),
        dict(name='src_system_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='record_status_cd', source='CHAR(1)', type='STRING', required=False),
        dict(name='effective_date', source='DATE', type='DATE', required=False),
        dict(name='expiry_date', source='DATE', type='DATE', required=False),
        dict(name='network_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='card_status_cd', source='VARCHAR(10)', type='STRING', required=False),
        dict(name='card_expiry_date', source='DATE', type='DATE', required=False),
        dict(name='contactless_ind', source='CHAR(1)', type='STRING', required=False),
        dict(name='online_enabled_ind', source='CHAR(1)', type='STRING', required=False),
    ]),
    'nvb_edw.dim_merchant': dict(name='edw_dim_merchant', keys=["merchant_id"], columns=[
        dict(name='merchant_id', source='INTEGER', type='INT', required=False),
        dict(name='merchant_name', source='VARCHAR(40)', type='STRING', required=False),
        dict(name='category', source='VARCHAR(15)', type='STRING', required=False),
        dict(name='batch_id', source='BIGINT', type='BIGINT', required=True),
        dict(name='load_ts', source='TIMESTAMP(0)', type='TIMESTAMP_NTZ', required=True),
        dict(name='src_system_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='record_status_cd', source='CHAR(1)', type='STRING', required=False),
        dict(name='effective_date', source='DATE', type='DATE', required=False),
        dict(name='expiry_date', source='DATE', type='DATE', required=False),
        dict(name='country_cd', source='CHAR(2)', type='STRING', required=False),
        dict(name='settlement_currency_cd', source='CHAR(3)', type='STRING', required=False),
        dict(name='acquirer_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='merchant_status_cd', source='VARCHAR(10)', type='STRING', required=False),
    ]),
    'nvb_edw.dim_product': dict(name='edw_dim_product', keys=["product_id"], columns=[
        dict(name='product_id', source='INTEGER', type='INT', required=False),
        dict(name='product_name', source='VARCHAR(30)', type='STRING', required=False),
        dict(name='annual_rate', source='DECIMAL(6,2)', type='DECIMAL(6,2)', required=False),
        dict(name='batch_id', source='BIGINT', type='BIGINT', required=True),
        dict(name='load_ts', source='TIMESTAMP(0)', type='TIMESTAMP_NTZ', required=True),
        dict(name='src_system_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='record_status_cd', source='CHAR(1)', type='STRING', required=False),
        dict(name='effective_date', source='DATE', type='DATE', required=False),
        dict(name='expiry_date', source='DATE', type='DATE', required=False),
        dict(name='product_family_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='currency_cd', source='CHAR(3)', type='STRING', required=False),
        dict(name='product_status_cd', source='VARCHAR(10)', type='STRING', required=False),
        dict(name='rate_basis_cd', source='VARCHAR(8)', type='STRING', required=False),
        dict(name='minimum_balance', source='DECIMAL(18,2)', type='DECIMAL(18,2)', required=False),
    ]),
    'nvb_edw.fct_daily_balance': dict(name='edw_fct_daily_balance', keys=["account_id","balance_date"], columns=[
        dict(name='account_id', source='INTEGER', type='INT', required=False),
        dict(name='balance_date', source='DATE', type='DATE', required=False),
        dict(name='balance', source='DECIMAL(18,2)', type='DECIMAL(18,2)', required=False),
        dict(name='batch_id', source='BIGINT', type='BIGINT', required=True),
        dict(name='load_ts', source='TIMESTAMP(0)', type='TIMESTAMP_NTZ', required=True),
        dict(name='src_system_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='record_status_cd', source='CHAR(1)', type='STRING', required=False),
        dict(name='effective_date', source='DATE', type='DATE', required=False),
        dict(name='expiry_date', source='DATE', type='DATE', required=False),
        dict(name='balance_type_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='currency_cd', source='CHAR(3)', type='STRING', required=False),
        dict(name='accrued_interest', source='DECIMAL(18,2)', type='DECIMAL(18,2)', required=False),
        dict(name='reserved_amount', source='DECIMAL(18,2)', type='DECIMAL(18,2)', required=False),
    ]),
    'nvb_edw.fct_loan': dict(name='edw_fct_loan', keys=["loan_id"], columns=[
        dict(name='loan_id', source='INTEGER', type='INT', required=False),
        dict(name='account_id', source='INTEGER', type='INT', required=False),
        dict(name='outstanding', source='DECIMAL(18,2)', type='DECIMAL(18,2)', required=False),
        dict(name='arrears_days', source='INTEGER', type='INT', required=False),
        dict(name='batch_id', source='BIGINT', type='BIGINT', required=True),
        dict(name='load_ts', source='TIMESTAMP(0)', type='TIMESTAMP_NTZ', required=True),
        dict(name='src_system_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='record_status_cd', source='CHAR(1)', type='STRING', required=False),
        dict(name='effective_date', source='DATE', type='DATE', required=False),
        dict(name='expiry_date', source='DATE', type='DATE', required=False),
        dict(name='loan_status_cd', source='VARCHAR(10)', type='STRING', required=False),
        dict(name='currency_cd', source='CHAR(3)', type='STRING', required=False),
        dict(name='contractual_rate', source='DECIMAL(6,2)', type='DECIMAL(6,2)', required=False),
        dict(name='repayment_frequency_cd', source='VARCHAR(10)', type='STRING', required=False),
        dict(name='collateral_type_cd', source='VARCHAR(12)', type='STRING', required=False),
    ]),
    'nvb_edw.fct_product_order': dict(name='edw_fct_product_order', keys=["order_id"], columns=[
        dict(name='order_id', source='INTEGER', type='INT', required=False),
        dict(name='customer_id', source='INTEGER', type='INT', required=False),
        dict(name='product_id', source='INTEGER', type='INT', required=False),
        dict(name='order_date', source='DATE', type='DATE', required=False),
        dict(name='status', source='VARCHAR(10)', type='STRING', required=False),
        dict(name='batch_id', source='BIGINT', type='BIGINT', required=True),
        dict(name='load_ts', source='TIMESTAMP(0)', type='TIMESTAMP_NTZ', required=True),
        dict(name='src_system_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='record_status_cd', source='CHAR(1)', type='STRING', required=False),
        dict(name='effective_date', source='DATE', type='DATE', required=False),
        dict(name='expiry_date', source='DATE', type='DATE', required=False),
        dict(name='application_channel_cd', source='VARCHAR(10)', type='STRING', required=False),
        dict(name='decision_system_cd', source='VARCHAR(12)', type='STRING', required=False),
        dict(name='currency_cd', source='CHAR(3)', type='STRING', required=False),
        dict(name='manual_review_ind', source='CHAR(1)', type='STRING', required=False),
    ]),
    'nvb_stg.customer_feed': dict(name='stg_customer_feed', keys=["customer_id"], columns=[
        dict(name='customer_id', source='VARCHAR(80)', type='STRING', required=False),
        dict(name='customer_name', source='VARCHAR(160)', type='STRING', required=False),
        dict(name='branch_id', source='VARCHAR(80)', type='STRING', required=False),
        dict(name='town', source='VARCHAR(80)', type='STRING', required=False),
        dict(name='updated_at', source='VARCHAR(26)', type='STRING', required=False),
    ]),
    'nvb_stg.transaction_feed': dict(name='stg_transaction_feed', keys=["transaction_id"], columns=[
        dict(name='transaction_id', source='VARCHAR(80)', type='STRING', required=False),
        dict(name='account_id', source='VARCHAR(80)', type='STRING', required=False),
        dict(name='txn_date', source='VARCHAR(80)', type='STRING', required=False),
        dict(name='amount', source='VARCHAR(80)', type='STRING', required=False),
        dict(name='channel', source='VARCHAR(80)', type='STRING', required=False),
        dict(name='merchant_id', source='VARCHAR(80)', type='STRING', required=False),
    ]),
}


def test_all_csv_inputs_and_no_outputs():
    entries = [e for e in MANIFEST['files'] if e.get('format') == 'csv' and e['path'].startswith('inputs/')]
    assert {e['table'] for e in entries} == set(SOURCE)
    assert LOAD.count('FROM read_files(') == len(entries)
    for entry in entries:
        spec = SOURCE[entry['table']]
        assert entry['columns'] == [c['name'] for c in spec['columns']]
        assert f"/{entry['table']}/{entry['table']}.csv" in LOAD
        assert f"WHEN 'D0' THEN {entry['row_count']} END" in CHECK
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
                  'current_timestamp()', "Unknown captured drop", "DATE '2025-06-30'", '20250630']:
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
    assert job['parameters'] == [{'name': 'drop', 'default': 'D0'}]
    assert job['tasks'][1]['depends_on'] == [{'task_key': 'load_inputs'}]
    for task, filename in zip(job['tasks'], ['load.sql', 'self_check.sql']):
        assert task['sql_task']['file'] == {'path': './' + filename, 'source': 'WORKSPACE'}
        assert task['sql_task']['parameters']['drop'] == '{{job.parameters.drop}}'
        assert task['sql_task']['warehouse_id'] == '${var.warehouse_id}'
    readme = (UNIT / 'README.md').read_text()
    assert all(t in readme and s['name'] in readme for t, s in SOURCE.items())
