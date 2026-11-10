import json
import re
import sys
from pathlib import Path

UNIT = Path(__file__).resolve().parent
ROOT = UNIT.parents[3]
sys.path.insert(0, str(ROOT / 'ci'))
SQL = (UNIT / 'transactions.pipeline.sql').read_text()
SPEC = json.loads((UNIT / 'unit.json').read_text())
CONTRACT = json.loads((ROOT / 'src/parity/contract.json').read_text())
ENTRY = next(e for e in CONTRACT['outputs'] if e['table'] == 'silver.fct_transaction')
DEFAULTS = {
    'src_system_cd': "'COREBANK'", 'record_status_cd': "'A'",
    'effective_date': "DATE '2024-01-01'", 'expiry_date': "DATE '9999-12-31'",
    'currency_cd': "'NOK'", 'posting_status_cd': "'POSTED'", 'reversal_ind': "'N'",
    'clearing_scheme_cd': "'DOMESTIC'",
}


def statement(header):
    return SQL.split(header, 1)[1].split(';\n', 1)[0]


def output_columns():
    declaration = statement('CREATE OR REFRESH MATERIALIZED VIEW fct_transaction (').split('CONSTRAINT')[0]
    return [tuple(line.strip().rstrip(',').split(' ', 1)) for line in declaration.strip().splitlines()]


def test_registry_entry():
    assert SPEC == {
        'job': 'silver_pipeline', 'legacy_unit': 'transactions',
        'outputs': ['silver.fct_transaction'], 'owns': ['silver.fct_transaction_quarantine'],
        'depends_on': ['silver_pipeline', 'silver_merchant'],
    }
    assert ENTRY['unit'] == 'transactions'


def test_schema_is_ddl_order_with_load_ts_after_batch_id():
    columns = output_columns()
    names = [name for name, _ in columns]
    golden = [c['name'] for c in ENTRY['columns']]
    assert [n for n in names if n != 'load_ts'] == golden
    assert names.index('load_ts') == names.index('batch_id') + 1
    types = dict(columns)
    assert types['transaction_id'] == 'BIGINT' and types['batch_id'] == 'BIGINT'
    assert types['account_id'] == types['merchant_id'] == 'INT'
    assert types['amount'] == 'DECIMAL(18,2)' and types['txn_date'] == 'DATE'
    assert types['load_ts'] == 'TIMESTAMP_NTZ'
    assert "TBLPROPERTIES ('delta.feature.timestampNtz'='supported')" in statement(
        'CREATE OR REFRESH MATERIALIZED VIEW fct_transaction (')


def test_projection_follows_declaration_and_defaults():
    body = statement('CREATE OR REFRESH MATERIALIZED VIEW fct_transaction (').split('AS SELECT', 1)[1]
    projection = body.split('FROM transaction_checked', 1)[0]
    aliases = re.findall(r'(?:AS |p\.|d\.)([a-z_]+)\s*(?:,|$)', projection, re.M)
    assert aliases == [name for name, _ in output_columns()]
    for column, literal in DEFAULTS.items():
        assert f'{literal} AS {column}' in projection
    assert 'd.batch_id' in projection and '20250630' not in SQL
    assert "DATE '2025-06-30'" not in SQL


def test_cleansing_matches_legacy_expressions():
    checked = statement('CREATE OR REFRESH PRIVATE MATERIALIZED VIEW transaction_checked')
    for expr in ['try_cast(trim(transaction_id) AS BIGINT)', 'try_cast(trim(account_id) AS INT)',
                 'try_cast(trim(txn_date) AS DATE)', 'try_cast(trim(merchant_id) AS INT)',
                 "try_cast(replace(trim(amount), ',', '.') AS DECIMAL(18,2))",
                 "coalesce(nullif(upper(trim(channel)), ''), 'ONLINE')"]:
        assert expr in checked
    assert 'bround' not in SQL and 'DOUBLE' not in SQL


def test_delivery_controls_reject_whole_delivery():
    checked = statement('CREATE OR REFRESH PRIVATE MATERIALIZED VIEW transaction_checked')
    for reason in ['invalid transaction_id', 'invalid amount', 'invalid txn_date', 'txn_date outside window',
                   'unknown account_id', 'unknown merchant_id', 'invalid channel', 'duplicate transaction_id']:
        assert f"'{reason}'" in checked
    assert 'p.transaction_id <= 0' in checked
    assert 'NOT BETWEEN d.window_start AND d.as_of_date' in checked
    assert "NOT IN ('CARD', 'MOBILE', 'BRANCH', 'ONLINE')" in checked
    assert 'count(*) OVER (PARTITION BY p.transaction_id) > 1' in checked
    assert 'asof_edw_dim_account(:drop)' in checked and 'FROM ${catalog}.${silver_schema}.dim_merchant)' in checked
    assert 'dim_account AS' not in checked.replace('asof_edw_dim_account', '')
    output = statement('CREATE OR REFRESH MATERIALIZED VIEW fct_transaction (')
    assert 'WHERE reject_reason IS NOT NULL) > 0' in output and 'raise_error(' in output
    assert output.count('ON VIOLATION FAIL UPDATE') == 6 and 'DROP ROW' not in SQL
    quarantine = statement('CREATE OR REFRESH MATERIALIZED VIEW fct_transaction_quarantine')
    assert 'WHERE reject_reason IS NOT NULL' in quarantine


def test_parameters_come_from_the_selected_drop():
    delivery = statement('CREATE OR REFRESH PRIVATE MATERIALIZED VIEW transaction_delivery')
    assert 'max(_business_date) AS as_of_date' in delivery and 'max(_batch_id) AS batch_id' in delivery
    assert "DATE '2024-01-01' AS window_start" in delivery
    assert 'resolve_drop(:drop)' in delivery
    assert '${drop}' not in SQL and 'TEMPORARY' not in SQL
    feed = statement('CREATE OR REFRESH PRIVATE MATERIALIZED VIEW transaction_feed')
    assert 'asof_stg_transaction_feed(:drop)' in feed
    assert 'silver_bad_rows' not in SQL


def test_registry_validates_with_upstream():
    from registry import load, validate, with_dependencies
    if not (ROOT / 'src/sql/units/silver_merchant/unit.json').exists():
        return
    registry = load(ROOT)
    validate(registry, CONTRACT)
    assert {'silver_merchant', 'sql_macros', 'bronze_landing'} <= set(with_dependencies(registry, ['silver_transactions']))
