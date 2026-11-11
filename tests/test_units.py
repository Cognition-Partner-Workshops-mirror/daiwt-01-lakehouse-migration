from pathlib import Path
from migration_ci.offline import check, summary

def test_units(request):
    goldens = request.config.getoption("--goldens")
    rows = check(Path(__file__).resolve().parents[1], goldens=goldens, structural=goldens is None)
    summary(rows)
    assert not any(row["status"] == "FAIL" for row in rows)
