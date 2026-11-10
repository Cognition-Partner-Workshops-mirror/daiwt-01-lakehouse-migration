"""Select builder units against the PR base and include their inputs."""
import json
import re
import subprocess

from classify_changes import classify_diff, expand


def required(suffix):
    base = 'origin/main'
    try:
        response = subprocess.check_output(
            ['gh', 'pr', 'view', suffix[3:], '--json', 'baseRefName'], text=True)
        branch = json.loads(response)['baseRefName']
        subprocess.run(['git', 'check-ref-format', 'refs/heads/' + branch], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = 'origin/' + branch
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError):
        pass
    return classify_diff(base, 'HEAD')


def select(registry, suffix, explicit=''):
    return expand(registry, re.split(r'[,\s]+', explicit.strip())) if explicit else required(suffix)['units']
