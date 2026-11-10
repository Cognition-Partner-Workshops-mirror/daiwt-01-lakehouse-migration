import subprocess
from types import SimpleNamespace

import pytest

from cleanup import MARKER, cleanup_pr


def schemas():
    return [SimpleNamespace(name=f"{tier}_pr_9001", comment=MARKER)
            for tier in ("bronze", "silver", "gold")]


@pytest.mark.parametrize("drop_failure,destroy_failure", [
    (False, False), (True, False), (False, True), (True, True)])
def test_schema_drops_precede_destroy_and_fail_independently(monkeypatch, drop_failure, destroy_failure):
    calls = []
    def sql(statement):
        calls.append(statement)
        if drop_failure and "bronze_pr_9001" in statement:
            raise RuntimeError("drop failed")
    def destroy(command, check):
        assert check is True
        calls.append(command)
        if destroy_failure:
            raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(subprocess, "run", destroy)
    errors = cleanup_pr(SimpleNamespace(sql=sql), "pr_9001", schemas())
    assert calls[:3] == [
        f"DROP SCHEMA IF EXISTS `daiwt_01_mig_dev`.`{tier}_pr_9001` CASCADE"
        for tier in ("bronze", "silver", "gold")]
    assert calls[3] == ["databricks", "bundle", "destroy", "-t", "dev", "--var",
                        "schema_suffix=pr_9001", "--auto-approve"]
    assert len(errors) == int(drop_failure) + int(destroy_failure)


@pytest.mark.parametrize("suffix", ["prod", "pr_1;DROP", "", None, "silver_pr_1"])
def test_unsafe_suffix_never_reaches_sql_or_destroy(monkeypatch, suffix):
    def unexpected(*args, **kwargs):
        pytest.fail("unsafe cleanup attempted")
    monkeypatch.setattr(subprocess, "run", unexpected)
    with pytest.raises(ValueError, match="invalid PR suffix"):
        cleanup_pr(SimpleNamespace(sql=unexpected), suffix, schemas())


def test_only_matching_marked_pr_schemas_are_dropped(monkeypatch):
    calls = []
    extra = [SimpleNamespace(name=name, comment=comment) for name, comment in [
        ("silver", MARKER), ("gold_pr_1", MARKER), ("other_pr_9001", MARKER),
        ("silver_pr_9001", "not our bundle"), ("pr_2_gold_pr_9001", MARKER)]]
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    assert cleanup_pr(SimpleNamespace(sql=calls.append), "pr_9001", extra) == []
    assert calls == []
