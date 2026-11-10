import json
import subprocess

import pytest

from registry import with_dependencies
from select_units import select

REGISTRY = {
    "upstream": {"depends_on": []},
    "chosen": {"depends_on": ["upstream"]},
    "downstream": {"depends_on": ["chosen"]},
    "unrelated": {"depends_on": []},
}


@pytest.mark.parametrize("fallback", [False, True])
def test_pr_base_and_upstream_selection(monkeypatch, fallback):
    calls = []
    def output(args, **kwargs):
        calls.append(args)
        if args[0] == "gh":
            if fallback:
                raise subprocess.CalledProcessError(1, args)
            return json.dumps({"baseRefName": "pre-migration"})
        raise AssertionError(args)
    def classify(base, head):
        calls.append([base, head])
        return {'units': ['upstream', 'chosen']}
    monkeypatch.setattr('select_units.classify_diff', classify)
    monkeypatch.setattr(subprocess, "check_output", output)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    assert select(REGISTRY, "pr_5") == ["upstream", "chosen"]
    base = "origin/main" if fallback else "origin/pre-migration"
    assert calls[-1] == [base, 'HEAD']
    assert calls[0] == ["gh", "pr", "view", "5", "--json", "baseRefName"]


def test_explicit_and_empty_dependency_selection():
    assert select(REGISTRY, "pr_5", "chosen") == ["upstream", "chosen"]
    assert select(REGISTRY, "pr_5", "*") == ['unrelated', 'upstream', 'chosen', 'downstream']
    assert with_dependencies(REGISTRY, []) == []
    with pytest.raises(ValueError, match="unknown units"):
        with_dependencies(REGISTRY, ["missing"])
