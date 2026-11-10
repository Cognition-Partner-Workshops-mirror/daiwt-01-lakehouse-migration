"""Select builder units against the PR base and include their inputs."""
import json
import re
import subprocess

from classify_changes import classify, expand
from registry import with_dependencies


def select(registry, suffix, explicit=""):
    if explicit:
        units = expand(registry, re.split(r"[,\s]+", explicit.strip()))
    else:
        base = "origin/main"
        try:
            response = subprocess.check_output(
                ["gh", "pr", "view", suffix[3:], "--json", "baseRefName"],
                text=True)
            branch = json.loads(response)["baseRefName"]
            subprocess.run(["git", "check-ref-format", "refs/heads/" + branch], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            base = "origin/" + branch
        except (subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError):
            pass
        paths = subprocess.check_output(["git", "diff", "--name-only", f"{base}...HEAD"],
                                        text=True).splitlines()
        units = classify(registry, paths)
    return with_dependencies(registry, units)
