import json
from pathlib import Path
import shutil

import duckdb
import pytest
import sqlglot

from migration_ci.goldens import columns, read_csv
from migration_ci.model import statements
from migration_ci.offline import check, compare, load_table


SQL = Path(__file__).with_name('01_customer.sql').read_text()


@pytest.fixture
def db():
    with duckdb.connect() as connection:
        connection.execute('CREATE SCHEMA bronze; CREATE SCHEMA silver')
        connection.execute('CREATE TABLE bronze.dim_branch (branch_id INT)')
        connection.execute('INSERT INTO bronze.dim_branch VALUES (1), (2)')
        connection.execute('''CREATE TABLE bronze.customer_feed (
            customer_id VARCHAR, customer_name VARCHAR, branch_id VARCHAR,
            town VARCHAR, updated_at VARCHAR)''')
        yield connection


def run(db):
    for statement in statements(SQL):
        db.execute(statement.sql(dialect='duckdb', unsupported_level=sqlglot.ErrorLevel.RAISE))


def feed(db, rows):
    db.execute('DELETE FROM bronze.customer_feed')
    if rows:
        db.executemany('INSERT INTO bronze.customer_feed VALUES (?, ?, ?, ?, ?)', rows)


def test_clean_merge_and_defaults(db):
    feed(db, [(' 1 ', '\u00a0Alice\u00a0', ' 1 ', '\u00a0Oslo\u00a0', '2025-06-30 01:02:03.999')])
    run(db)
    row = db.execute('SELECT * FROM silver.dim_customer').fetchone()
    assert row[:4] == (1, 'Alice', 1, 'Oslo')
    assert row[4].strftime('%Y-%m-%dT%H:%M:%S.000Z') == '2025-06-30T01:02:03.000Z'
    assert row[4].microsecond == 0
    assert row[5] == 20250630
    assert row[6].strftime('%Y-%m-%dT%H:%M:%S.000Z') == '2025-06-30T23:00:00.000Z'
    assert row[7:9] == ('COREBANK', 'A')
    assert str(row[9]) == '2024-01-01'
    assert str(row[10]) == '9999-12-31'
    assert row[11:] == ('PERSONAL', 'NO', 'nb', 'VERIFIED', 'N')
    db.execute("UPDATE silver.dim_customer SET kyc_status_cd = 'REVIEW'")
    feed(db, [('1', 'Alice Smith', '2', '', '2025-07-01 00:00:00'),
              ('2', 'Bob', '1', None, '2025-07-01 00:00:00')])
    run(db)
    assert db.execute('SELECT customer_id, customer_name, branch_id, town, kyc_status_cd '
                      'FROM silver.dim_customer ORDER BY customer_id').fetchall() == [
        (1, 'Alice Smith', 2, '', 'REVIEW'), (2, 'Bob', 1, None, 'VERIFIED')]
    run(db)
    assert db.execute('SELECT COUNT(*) FROM silver.dim_customer').fetchone() == (2,)
    feed(db, [])
    run(db)
    assert db.execute('SELECT COUNT(*) FROM silver.dim_customer').fetchone() == (2,)


def test_empty_initial_delivery(db):
    run(db)
    assert db.execute('SELECT COUNT(*) FROM silver.dim_customer').fetchone() == (0,)


def test_timestamp_types_are_timezone_naive(db):
    feed(db, [('1', 'Alice', '1', 'Oslo', '2025-06-30 00:00:00')])
    run(db)
    assert db.execute('SELECT typeof(updated_at), typeof(load_ts) FROM silver.dim_customer').fetchone() == (
        'TIMESTAMP', 'TIMESTAMP')


def test_all_deduplication_tiebreakers(db):
    feed(db, [
        ('1', 'ZZZ', '2', 'ZZZ', '2025-06-29 00:00:00'),
        ('1', 'Alpha', '2', 'ZZZ', '2025-06-30 00:00:00'),
        ('1', 'zeta', '2', 'Alpha', '2025-06-30 00:00:00'),
        ('1', 'zeta', '1', 'Zulu', '2025-06-30 00:00:00'),
        ('1', 'zeta', '2', 'Zulu', '2025-06-30 00:00:00'),
        ('1', 'zeta', '2', None, '2025-06-30 00:00:00'),
    ])
    run(db)
    assert db.execute('SELECT customer_name, town, branch_id FROM silver.dim_customer').fetchone() == (
        'zeta', 'Zulu', 2)


@pytest.mark.parametrize(('column', 'value'), [
    (0, None), (0, 'bad'), (0, '0'), (0, '-1'), (0, '2147483648'),
    (1, None), (1, ' \u00a0 '), (2, None), (2, 'bad'), (2, '999'),
    (4, None), (4, ''), (4, '2025-02-30 12:00:00'),
])
def test_reject_before_writing(db, column, value):
    feed(db, [('1', 'Alice', '1', 'Oslo', '2025-06-30 00:00:00')])
    run(db)
    before = db.execute('SELECT * FROM silver.dim_customer').fetchall()
    invalid = ['2', 'Bob', '1', 'Bergen', '2025-06-30 00:00:00']
    invalid[column] = value
    feed(db, [('3', 'Carol', '1', '', '2025-06-30 00:00:00'), invalid])
    with pytest.raises(duckdb.Error, match='Invalid customer delivery'):
        run(db)
    assert db.execute('SELECT * FROM silver.dim_customer').fetchall() == before


def test_d0_golden_parity():
    cache = Path(__file__).resolve().parents[3] / '.goldens'
    if not (cache / 'manifest.json').exists():
        pytest.skip('Customer golden fixtures are not cached')
    manifest = json.loads((cache / 'manifest.json').read_text())
    required = [e for e in manifest['files'] if e['path'] == 'outputs/D0/customer/dim_customer.csv'
                or (e['path'].startswith('inputs/D0/')
                    and e.get('source_table', '').split('.')[-1] in ('customer_feed', 'dim_branch'))]
    if len(required) != 3 or any(not (cache / e['path']).exists() for e in required):
        pytest.skip('Customer golden fixtures are not cached')
    with duckdb.connect() as connection:
        connection.execute('CREATE SCHEMA bronze; CREATE SCHEMA silver')
        for table in ('customer_feed', 'dim_branch'):
            entry = next(e for e in manifest['files']
                         if e['path'].startswith('inputs/D0/')
                         and e.get('source_table', '').split('.')[-1] == table)
            rows = read_csv((cache / entry['path']).read_bytes(), entry)
            load_table(connection, 'bronze.' + table, entry, rows, 'D0')
        run(connection)
        entry = next(e for e in manifest['files']
                     if e['path'] == 'outputs/D0/customer/dim_customer.csv')
        expected = read_csv((cache / entry['path']).read_bytes(), entry)
        projection = ', '.join(columns(entry))
        actual = connection.execute('SELECT ' + projection + ' FROM silver.dim_customer').fetchall()
        result = compare(expected, actual, entry)
        print(result)
        assert result['status'] == 'PASS'


def test_stock_harness_rejects_invalid_row(tmp_path):
    source = Path(__file__).parent
    directory = tmp_path / 'src/units/customer'
    directory.mkdir(parents=True)
    for name in ('unit.yml', '01_customer.sql'):
        shutil.copyfile(source / name, directory / name)
    shutil.copyfile(source.parents[2] / 'estate.yml', tmp_path / 'estate.yml')
    cache = tmp_path / '.goldens'
    files = [
        {'path': 'inputs/D0/customer_feed.csv', 'source_table': 'customer_feed',
         'columns': ['customer_id', 'customer_name', 'branch_id', 'town', 'updated_at']},
        {'path': 'inputs/D0/dim_branch.csv', 'source_table': 'dim_branch',
         'columns': [{'name': 'branch_id', 'type': 'int'}]},
        {'path': 'outputs/D0/customer/dim_customer.csv', 'table': 'silver.dim_customer',
         'columns': [{'name': 'customer_id', 'type': 'int'}]},
    ]
    data = [
        'customer_id,customer_name,branch_id,town,updated_at\n'
        'invalid,Alice,1,Oslo,2025-06-30 00:00:00\n',
        'branch_id\n1\n',
        'customer_id\n',
    ]
    for entry, text in zip(files, data):
        path = cache / entry['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (cache / 'manifest.json').write_text(json.dumps({'files': files}))
    result = check(root=tmp_path, goldens=cache, names=['customer'])
    print(result)
    assert len(result) == 1
    assert result[0]['status'] == 'FAIL'
    assert 'Invalid customer delivery' in result[0]['detail']
