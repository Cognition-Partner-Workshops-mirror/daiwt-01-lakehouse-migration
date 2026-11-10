import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
import re

import pytest

duckdb = pytest.importorskip("duckdb")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SQL = (HERE / "daily_balance.pipeline.sql").read_text()
EXPECTED = json.loads((HERE / "expected.json").read_text())
COLUMNS = [
    "account_id", "balance_date", "balance", "batch_id", "load_ts",
    "src_system_cd", "record_status_cd", "effective_date", "expiry_date",
    "balance_type_cd", "currency_cd", "accrued_interest", "reserved_amount",
]
ROW = ("1", "2024-01-01", "0", "20250630", "2025-06-30 23:00:00",
       "COREBANK", "A", "2024-01-01", "9999-12-31", "BOOK", "NOK", "0", "0")


def run(db, bad_rows=False):
    statements = SQL.strip().split(";")[:3]
    for statement in statements:
        name = re.search(r"MATERIALIZED VIEW (\w+)", statement)[1]
        query = statement.split("AS SELECT", 1)[1]
        query = "SELECT" + query
        query = query.replace("${catalog}.${silver_schema}.asof_edw_fct_daily_balance(:drop)", "source")
        query = query.replace("${silver_bad_rows}", str(bad_rows).lower())
        query = query.replace("TIMESTAMP_NTZ", "TIMESTAMP").replace("AS STRING", "AS VARCHAR")
        if name == "fct_daily_balance":
            constraints = re.findall(r"EXPECT \((.*?)\) ON VIOLATION DROP ROW", statement)
            assert len(constraints) == 3
            query += " WHERE " + " AND ".join(f"({c})" for c in constraints)
        db.execute(f"CREATE OR REPLACE TABLE {name} AS {query}")


def source(db, rows):
    db.execute("CREATE TABLE source (" + ",".join(f"{c} VARCHAR" for c in COLUMNS) + ")")
    if rows:
        db.executemany("INSERT INTO source VALUES (" + ",".join("?" for _ in COLUMNS) + ")", rows)


def test_types_scale_status_and_duplicates():
    with duckdb.connect() as db:
        deleted = list(ROW)
        deleted[6] = "D"
        source(db, [ROW, ROW, deleted])
        run(db)
        schema = db.execute("DESCRIBE fct_daily_balance").fetchall()
        assert [r[0] for r in schema] == COLUMNS
        assert [r[1] for r in schema] == [
            "INTEGER", "DATE", "DECIMAL(18,2)", "BIGINT", "TIMESTAMP", "VARCHAR",
            "VARCHAR", "DATE", "DATE", "VARCHAR", "VARCHAR", "DECIMAL(18,2)", "DECIMAL(18,2)",
        ]
        assert db.execute("SELECT count(*) FROM fct_daily_balance").fetchone()[0] == 3
        assert db.execute("SELECT balance, accrued_interest, reserved_amount FROM fct_daily_balance LIMIT 1").fetchone() == (Decimal("0.00"),) * 3
        assert db.execute("SELECT count(*) FROM daily_balance_quarantine").fetchone()[0] == 0


@pytest.mark.parametrize("bad_rows", [False, True])
def test_empty_and_switch(bad_rows):
    with duckdb.connect() as db:
        source(db, [])
        run(db, bad_rows)
        assert db.execute("SELECT count(*) FROM fct_daily_balance").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM daily_balance_quarantine").fetchone()[0] == int(bad_rows)


def test_ddl_constraints_and_nullable_columns():
    rows = [ROW]
    for index, value in [(3, None), (4, None), (6, "X"), (6, None), (0, None), (2, None)]:
        row = list(ROW)
        row[index] = value
        rows.append(row)
    with duckdb.connect() as db:
        source(db, rows)
        run(db)
        assert db.execute("SELECT count(*) FROM fct_daily_balance").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM daily_balance_quarantine").fetchone()[0] == 3


def test_invalid_cast_fails():
    with duckdb.connect() as db:
        row = list(ROW)
        row[2] = "not-a-decimal"
        source(db, [row])
        with pytest.raises(duckdb.ConversionException):
            run(db)


def test_d0_control_metrics():
    path = Path(os.environ.get("DAILY_BALANCE_D0_CSV", ROOT / "golden/inputs/D0/nvb_edw.fct_daily_balance/nvb_edw.fct_daily_balance.csv"))
    if not path.is_file():
        pytest.skip("Provide DAILY_BALANCE_D0_CSV or the captured golden input")
    manifest = json.loads((ROOT / "golden/manifest.json").read_text())
    entry = next(e for e in manifest["files"] if e["path"].endswith("nvb_edw.fct_daily_balance.csv"))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    with duckdb.connect() as db:
        db.read_csv(str(path), delimiter="|", header=True, all_varchar=True).create_view("source")
        run(db)
        assert db.execute("SELECT count(*) FROM fct_daily_balance").fetchone()[0] == EXPECTED["rows"]
        for column, expected in EXPECTED["distinct"].items():
            assert db.execute(f"SELECT count(DISTINCT {column}) FROM fct_daily_balance").fetchone()[0] == expected
        for column, expected in EXPECTED["sums"].items():
            assert db.execute(f"SELECT sum({column}) FROM fct_daily_balance").fetchone()[0] == Decimal(str(expected))
        for column, bounds in EXPECTED["dates"].items():
            lo, hi = db.execute(f"SELECT min({column}), max({column}) FROM fct_daily_balance").fetchone()
            assert {"min": str(lo), "max": str(hi)} == bounds
        for key, counts in EXPECTED.items():
            if key.endswith("_counts"):
                column = key.removesuffix("_counts")
                assert dict(db.execute(f"SELECT {column}, count(*) FROM fct_daily_balance GROUP BY {column}").fetchall()) == counts
        assert db.execute("SELECT count(*) FROM daily_balance_quarantine").fetchone()[0] == 0
        assert db.execute("SELECT min(n), max(n), min(day_count), max(day_count) FROM (SELECT count(*) n, count(DISTINCT balance_date) day_count FROM fct_daily_balance GROUP BY account_id)").fetchone() == (547, 547, 547, 547)


def test_lakeflow_contract():
    assert "TEMPORARY VIEW" not in SQL
    assert SQL.count("TBLPROPERTIES ('delta.feature.timestampNtz'='supported')") == 3
    assert "PRIVATE MATERIALIZED VIEW daily_balance_candidates" in SQL
    metadata = json.loads((HERE / "unit.json").read_text())
    assert metadata["owns"] == ["silver.fct_daily_balance", "silver.daily_balance_quarantine"]
    assert metadata["depends_on"] == ["silver_pipeline"]
