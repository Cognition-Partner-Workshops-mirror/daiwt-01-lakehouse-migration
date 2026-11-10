import importlib.util
import shutil
from pathlib import Path


def module_loader(tmp_path):
    path = Path(__file__).resolve().parents[1] / "silver_resources.py"
    scaffold = tmp_path / 'src/sql/units/silver_pipeline'
    scaffold.mkdir(parents=True, exist_ok=True)
    shutil.copy(path.parent / 'src/sql/units/silver_pipeline/silver_pipeline.job.yml', scaffold)
    spec = importlib.util.spec_from_file_location("silver_resources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__file__ = str(tmp_path / "silver_resources.py")
    return module


def loader(tmp_path):
    return module_loader(tmp_path).load_resources


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
