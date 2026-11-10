import json
from pathlib import Path

import pytest

from cleanup import drop_tables
from registry import load, tables, topo_order, validate
from run_units import run_units

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = {'outputs': [{'table': 'gold.city', 'unit': 'foundation'}]}


def spec(job="build", outputs=None, owns=None, depends_on=None):
    return dict(job=job, legacy_unit='foundation', outputs=outputs or [], owns=owns or [], depends_on=depends_on or [])


def test_real_registry():
    registry = load(ROOT)
    validate(registry, json.loads((ROOT / 'src/parity/contract.json').read_text()))
    import yaml
    for unit, spec in registry.items():
        jobs = {}
        directory = spec['job'] if spec['job'] == 'silver_pipeline' else unit
        for path in (ROOT / "src/sql/units" / directory).glob("*.job.yml"):
            jobs.update(yaml.safe_load(path.read_text()).get("resources", {}).get("jobs", {}))
        assert spec["job"] in jobs


def test_valid():
    registry = {"silver": spec(owns=["silver.city", "bronze.city"]),
                "gold": spec("gold", ["gold.city"], depends_on=["silver"])}
    validate(registry, CONTRACT)
    assert tables(registry["silver"]) == ["silver.city", "bronze.city"]
    validate({}, CONTRACT)


def test_foundation_is_explicit_without_contract_output():
    contract = {'outputs': [{'table': 'silver.dim_customer', 'unit': 'customer'}]}
    validate({'shared': spec()}, contract)
    with pytest.raises(ValueError, match='another legacy_unit'):
        validate({'shared': spec(outputs=['silver.dim_customer'])}, contract)
    with pytest.raises(ValueError, match='legacy_unit not in contract'):
        validate({'bad': {**spec(), 'legacy_unit': 'unknown'}}, contract)


@pytest.mark.parametrize("registry,match", [
    ([], "JSON object"),
    ({"Bad": spec()}, "unit name"),
    ({"a": spec(job="")}, "non-empty"),
    ({"a": spec(job=1)}, "non-empty"),
    ({"a": spec(job="bad-key")}, "invalid job identifier"),
    ({"a": spec(), "b": spec()}, "duplicate job"),
    ({"a": spec(outputs=["gold.city"]), "b": spec("b", ["gold.city"])}, "owned by both"),
    ({"a": spec(owns=["silver.city"]), "b": spec("b", owns=["silver.city"])}, "owned by both"),
    ({"a": spec(outputs=["silver.city"]), "b": spec("b", owns=["silver.city"])}, "not in contract"),
    ({"a": spec(outputs=["gold.missing"])}, "not in contract"),
    ({"a": spec(owns=["gold.city"])}, "belongs in outputs"),
    ({"a": spec(depends_on=["missing"])}, "unknown dependency"),
    ({"a": spec(depends_on=["a"])}, "self-dependency"),
    ({"a": spec(depends_on=["b"]), "b": spec("b", depends_on=["a"])}, "a -> b -> a"),
    ({"a": spec(depends_on=["b"]), "b": spec("b", depends_on=["c"]),
      "c": spec("c", depends_on=["a"])}, "a -> b -> c -> a"),
    ({"a": {"job": "a"}}, "required keys"),
    ({"a": {**spec(), "extra": []}}, "required keys"),
    ({"a": spec(owns=["landing.city"])}, "invalid table"),
    ({"a": spec(owns=["silver_pr_1.city"])}, "invalid table"),
    ({"a": spec(owns=["silver.City"])}, "invalid table"),
    ({"a": spec(outputs=["gold.city", "gold.city"])}, "duplicate table"),
    ({"a": spec(owns=["silver.city", "silver.city"])}, "duplicate table"),
])
def test_invalid(registry, match):
    with pytest.raises(ValueError, match=match):
        validate(registry, CONTRACT)


@pytest.mark.parametrize("field", ["outputs", "owns", "depends_on"])
@pytest.mark.parametrize("value", ["not a list", [1], None])
def test_bad_list(field, value):
    entry = spec()
    entry[field] = value
    with pytest.raises(ValueError, match="list of strings"):
        validate({"a": entry}, CONTRACT)


def test_cross_ownership():
    registry = {"a": spec(outputs=["gold.city"]), "b": spec("b", owns=["gold.city"])}
    with pytest.raises(ValueError, match="belongs in outputs"):
        validate(registry, CONTRACT)


def test_load_invalid(tmp_path):
    path = tmp_path / 'src/sql/units/broken'
    path.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        load(tmp_path)


@pytest.mark.parametrize("field", ["job", "legacy_unit", "outputs", "owns", "depends_on"])
def test_missing_key(field):
    entry = spec()
    del entry[field]
    with pytest.raises(ValueError, match="required keys"):
        validate({"a": entry}, CONTRACT)


def test_topology():
    registry = {"c": spec("c", depends_on=["b"]), "a": spec(),
                "b": spec("b", depends_on=["a"]), "z": spec("z")}
    assert topo_order(registry, "*") == ["a", "b", "c", "z"]
    assert topo_order(registry, ["c", "b"]) == ["b", "c"]
    assert topo_order(registry, []) == []
    assert topo_order({}, []) == []
    with pytest.raises(ValueError, match="unknown units"):
        topo_order(registry, ["unknown"])
    with pytest.raises(ValueError, match="cycle"):
        topo_order({"a": spec(depends_on=["a"])}, ["a"])


class Warehouse:
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.calls = rows, error, []

    def sql(self, statement):
        self.calls.append(statement)
        if statement.startswith("SELECT"):
            return self.rows
        if self.error:
            raise RuntimeError(self.error)
        return []


@pytest.mark.parametrize("kind,verb", [("TABLE", "TABLE"), ("MANAGED", "TABLE"),
    ("EXTERNAL", "TABLE"), ("VIEW", "VIEW"), ("MATERIALIZED_VIEW", "MATERIALIZED VIEW"),
    ("STREAMING_TABLE", "TABLE")])
@pytest.mark.parametrize("prod", [False, True])
def test_drop_types(kind, verb, prod):
    warehouse = Warehouse([["city", kind], ["unowned", "TABLE"]])
    registry = {"a": spec(outputs=["gold.city"], owns=["silver.city"])}
    drop_tables(warehouse, registry, suffix=None if prod else "pr_12", prod=prod)
    catalog = "daiwt_01_mig_prod" if prod else "daiwt_01_mig_dev"
    for layer in ("silver", "gold"):
        schema = layer if prod else layer + "_pr_12"
        assert f"DROP {verb} IF EXISTS `{catalog}`.`{schema}`.`city`" in warehouse.calls
        assert any(f"table_schema = '{schema}'" in sql for sql in warehouse.calls)
    assert not any("DROP SCHEMA" in sql or "unowned`" in sql for sql in warehouse.calls)


def test_drop_selection_and_empty():
    registry = {"a": spec(owns=["silver.city"]), "b": spec("b", ["gold.city"])}
    warehouse = Warehouse([["city", "TABLE"]])
    drop_tables(warehouse, registry, ["a"], "pr_1")
    assert len(warehouse.calls) == 2
    warehouse = Warehouse()
    drop_tables(warehouse, registry, "*", "pr_1")
    assert len(warehouse.calls) == 2
    assert all(sql.startswith("SELECT") for sql in warehouse.calls)
    warehouse = Warehouse()
    drop_tables(warehouse, {}, suffix="pr_1")
    assert warehouse.calls == []


def test_pipeline_skip(capsys):
    warehouse = Warehouse([["city", "TABLE"], ["other", "TABLE"]], "Table is managed by a Lakeflow pipeline")
    drop_tables(warehouse, {"a": spec(owns=["silver.city", "silver.other"])}, suffix="pr_1")
    assert len(warehouse.calls) == 3
    assert "SKIP" in capsys.readouterr().out


def test_drop_error():
    warehouse = Warehouse([["city", "TABLE"]], "permission denied")
    with pytest.raises(RuntimeError, match="permission denied"):
        drop_tables(warehouse, {"a": spec(owns=["silver.city"])}, suffix="pr_1")


def test_run_order(monkeypatch):
    from test_ci import mock_runs
    calls = []
    mock_runs(monkeypatch, calls)
    registry = {"b": spec("b", depends_on=["a"]), "a": spec("a")}
    run_units("pr_1", registry, ["b", "a"], drop="D0")
    assert [call[-1] for call in calls] == ["a", "b"]
