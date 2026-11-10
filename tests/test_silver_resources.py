import importlib.util
from pathlib import Path


def loader(tmp_path):
    path = Path(__file__).resolve().parents[1] / "silver_resources.py"
    spec = importlib.util.spec_from_file_location("silver_resources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__file__ = str(tmp_path / "silver_resources.py")
    return module.load_resources


def test_zero_entities_has_no_pipeline(tmp_path):
    assert loader(tmp_path)(None).pipelines == {}


def test_discovery_excludes_scaffold_and_non_entities(tmp_path):
    for name in ["silver_pipeline/placeholder.sql", "silver_pipeline/demo.pipeline.sql",
                 "silver_customer/customer.pipeline.sql", "gold_report/report.pipeline.sql"]:
        path = tmp_path / "src/sql/units" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SELECT 1")
    pipelines = loader(tmp_path)(None).pipelines
    assert list(pipelines) == ["silver"]
    assert [library.file.path for library in pipelines["silver"].libraries] == [
        "src/sql/units/silver_customer/customer.pipeline.sql"]
