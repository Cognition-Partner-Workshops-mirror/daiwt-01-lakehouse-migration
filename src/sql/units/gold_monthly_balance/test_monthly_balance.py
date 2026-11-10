import re
from pathlib import Path

import pytest
import yaml

duckdb = pytest.importorskip("duckdb")
HERE = Path(__file__).resolve().parent
SQL = (HERE / "monthly_balance.sql").read_text()
PARAMS = {"window_start": "2024-01-01", "as_of_date": "2025-06-30",
          "batch_id": "20250630", "load_ts": "2025-06-30 23:00:00"}


def statements():
    sql = re.sub(r"IDENTIFIER\(:catalog \|\| '\.' \|\| :\w+ \|\| '\.(\w+)'\)", r"\1", SQL)
    for name, value in PARAMS.items():
        sql = sql.replace(":" + name, f"'{value}'")
    sql = sql.replace("CREATE OR REPLACE TEMPORARY VIEW", "CREATE OR REPLACE VIEW")
    sql = sql.replace("trunc(b.balance_date, 'MM')", "CAST(date_trunc('month', b.balance_date) AS DATE)")
    sql = sql.replace("add_months(month_start, 1)", "(month_start + INTERVAL 1 MONTH)")
    sql = sql.replace("add_months(CAST(date_trunc('month', b.balance_date) AS DATE), 1)",
                      "(CAST(date_trunc('month', b.balance_date) AS DATE) + INTERVAL 1 MONTH)")
    sql = sql.replace("date_sub(", "date_add_days_back(")
    sql = re.sub(r"\(\s*account_id INT,.*?\) AS\nSELECT", "AS\nSELECT", sql, flags=re.S)
    return [s for s in sql.split(";") if s.strip()]


def run(daily, accounts):
    db = duckdb.connect()
    db.execute("CREATE MACRO date_add_days_back(d, n) AS CAST(d - n * INTERVAL 1 DAY AS DATE)")
    db.execute("CREATE MACRO raise_error(m) AS error(m)")
    db.execute("CREATE TABLE fct_daily_balance(account_id INT, balance_date DATE, balance DECIMAL(18,2), record_status_cd VARCHAR)")
    db.execute("CREATE TABLE dim_account(account_id INT, record_status_cd VARCHAR)")
    db.executemany("INSERT INTO fct_daily_balance VALUES (?,?,?,?)", daily)
    db.executemany("INSERT INTO dim_account VALUES (?,?)", accounts)
    for statement in statements():
        db.execute(statement)
    return db.execute("SELECT account_id, CAST(month_start AS VARCHAR), CAST(average_balance AS VARCHAR), "
                      "CAST(closing_balance AS VARCHAR), batch_id, src_system_cd, record_status_cd, "
                      "CAST(effective_date AS VARCHAR), CAST(expiry_date AS VARCHAR) "
                      "FROM mart_monthly_balance ORDER BY 1, 2").fetchall()


def month(account, year, month_no, days, balances, status="A"):
    return [(account, f"{year}-{month_no:02d}-{d:02d}", balances(d), status) for d in range(1, days + 1)]


def test_average_half_up_and_closing_balance():
    rows = month(1, 2024, 2, 29, lambda d: "0.01" if d == 1 else "0.00")
    rows[-1] = (1, "2024-02-29", "0.00", "A")
    rows += month(1, 2024, 4, 30, lambda d: "1.15" if d <= 1 else "0.00")
    rows[-1] = (1, "2024-04-30", "-3.00", "A")
    out = run(rows, [(1, "A")])
    assert out[0][:4] == (1, "2024-02-01", "0.00", "0.00")
    # 1.15 - 3.00 over 30 days = -0.0616.. -> -0.06
    assert out[1][:4] == (1, "2024-04-01", "-0.06", "-3.00")
    assert out[0][4:] == (20250630, "COREBANK", "A", "2024-01-01", "9999-12-31")


def test_half_cent_mean_rounds_half_up():
    # 30 days summing to 0.15 -> mean 0.005 -> 0.01 (HALF_EVEN would give 0.00)
    rows = month(1, 2025, 6, 30, lambda d: "0.15" if d == 1 else "0.00")
    assert run(rows, [(1, "A")])[0][2] == "0.01"


def test_incomplete_month_rejects():
    rows = month(1, 2025, 6, 29, lambda d: "1.00")
    with pytest.raises(Exception, match="incomplete daily ledger month"):
        run(rows, [(1, "A")])


def test_inactive_or_unknown_account_rejects_publication():
    rows = month(1, 2025, 6, 30, lambda d: "1.00") + month(2, 2025, 6, 30, lambda d: "1.00")
    with pytest.raises(Exception, match="account-month missing"):
        run(rows, [(1, "A"), (2, "D")])


def test_window_and_active_rows_only():
    rows = month(1, 2025, 6, 30, lambda d: "2.00")
    rows += month(1, 2023, 12, 31, lambda d: "9.00")
    with pytest.raises(Exception, match="account-month missing"):
        run(rows, [(1, "A")])
    assert len(run(month(1, 2025, 6, 30, lambda d: "2.00"), [(1, "A")])) == 1


def test_unit_job_contract():
    job = yaml.safe_load((HERE / "monthly_balance.job.yml").read_text())["resources"]["jobs"]
    spec = job["gold_monthly_balance"]
    assert spec["name"] == "${var.job_name_prefix}gold_monthly_balance"
    defaults = {p["name"]: p["default"] for p in spec["parameters"]}
    assert defaults == {"drop": "D0", **PARAMS}
    tasks = [t["task_key"] for t in spec["tasks"]]
    assert tasks == ["build_mart_monthly_balance", "verify_mart_monthly_balance"]
