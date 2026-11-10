"""Shared upstream-only selection and content-addressed verification policy."""
import hashlib
import json
import sys

from registry import git, load_at, with_dependencies

PREFIX = 'src/sql/units/'


def ignored(path):
    return path.startswith(('docs/', 'tests/', '.github/')) or path in (
        'README.md', '.gitignore', 'AGENTS.md')


def expand(registry, explicit):
    return with_dependencies(registry, registry if explicit == '*' or '*' in explicit else explicit)


def classify(registry, paths):
    selected, shared = set(), False
    for path in paths:
        if ignored(path):
            continue
        if path.startswith(PREFIX) and '/' in path[len(PREFIX):]:
            unit = path[len(PREFIX):].split('/')[0]
            if unit in registry:
                selected.add(unit)
        else:
            shared = True
    units = expand(registry, '*' if shared else selected)
    return {'required': shared or bool(units), 'units': units, 'shared_changed': shared}


def classify_diff(base, head):
    base = git('merge-base', base, head).strip()
    paths = git('diff', '--no-renames', '--name-only', base, head).splitlines()
    return classify(load_at(head), paths)


def fingerprint(rev, units):
    units = sorted(expand(load_at(rev), units))
    lines = []
    for entry in git('ls-tree', '-r', rev).splitlines():
        metadata, path = entry.split('\t', 1)
        if ignored(path):
            continue
        if path.startswith(PREFIX) and path[len(PREFIX):].split('/')[0] not in units:
            continue
        lines.append(f'{path}\t{metadata.split()[2]}')
    content = '\n'.join(sorted(lines)) + '\nunits\t' + json.dumps(units, separators=(',', ':')) + '\n'
    return hashlib.sha256(content.encode()).hexdigest()


if __name__ == '__main__':
    print(json.dumps(classify_diff(sys.argv[1], sys.argv[2])))
