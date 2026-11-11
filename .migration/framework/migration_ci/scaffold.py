import shutil
from importlib.metadata import version
from pathlib import Path

import yaml

from .model import estate

README = '''## Migration CI

Install `python -m pip install -r requirements-dev.txt`. Add a directory under
`src/units/<name>/` with `unit.yml` and ordered SQL files; no shared registry.
Run `migration-ci fetch-goldens` then `migration-ci check` (or
`migration-ci check --goldens /path/to/local/goldens`). `pytest --goldens PATH`
runs the same offline gate. Plain pytest validates structure without remote data.
PRs run offline checks only. Manually dispatch integrate for dev integration,
then promote for production. Reset destroys only the selected bundle and its
bronze/silver/gold/verification schemas, never the landing golden volume.
Framework fixes propagate by running `migration-ci sync .` from a new framework
version and opening a PR for the changed vendored files.
'''


def bundle_config(config):
    targets = {}
    for target in ('dev', 'prod'):
        targets[target] = {
            'permissions': [{'service_principal_name': '${workspace.current_user.userName}', 'level': 'CAN_MANAGE'},
                            {'group_name': 'users', 'level': 'CAN_VIEW'}],
            'workspace': {'root_path': '/Workspace/Users/${workspace.current_user.userName}/.bundle/${bundle.name}/' + target + '${var.schema_suffix}'},
            'variables': {'catalog': config[target + '_catalog']},
        }
    targets['dev']['default'] = True
    targets['dev']['presets'] = {'trigger_pause_status': 'PAUSED'}
    targets['prod']['mode'] = 'production'
    return {'bundle': {'name': config['estate'] + '-migration', 'databricks_cli_version': '>= 1.25.0'},
            'python': {'resources': ['migration_resources:load_resources']},
            'sync': {'include': ['.migration/framework/**', 'estate.yml']},
            'variables': {'catalog': {'description': 'Target catalog'},
                          'schema_suffix': {'description': 'Optional isolated schema suffix, e.g. _pilot', 'default': ''},
                          'warehouse_id': {'default': config['warehouse_id']},
                          'golden_path': {'default': config['golden_path']}},
            'targets': targets}


def workflows():
    auth = {'DATABRICKS_AUTH_TYPE': 'github-oidc', 'DATABRICKS_HOST': '${{ vars.DATABRICKS_HOST }}',
            'DATABRICKS_CLIENT_ID': '${{ vars.DATABRICKS_CLIENT_ID }}'}
    setup = [{'uses': 'actions/checkout@v4', 'with': {'fetch-depth': 0}},
             {'uses': 'actions/setup-python@v5', 'with': {'python-version': '3.11'}},
             {'run': 'python -m pip install -r requirements-dev.txt'},
             {'uses': 'databricks/setup-cli@main', 'with': {'version': '1.25.0'}}]
    def workflow(name, trigger, environment, steps):
        return {'name': name, 'on': trigger, 'permissions': {'contents': 'read', 'id-token': 'write'},
                'jobs': {'migration': {'runs-on': 'ubuntu-latest', 'environment': environment,
                                       'env': auth, 'steps': setup + steps}}}
    pr = workflow('Migration PR', {'pull_request': {}}, 'dev', [
        {'run': 'python -m pytest -q'},
        {'run': 'migration-ci fetch-goldens --changed-from "$BASE_REF"',
         'env': {'BASE_REF': '${{ github.event.pull_request.base.sha }}'}},
        {'run': 'migration-ci check --changed-from "$BASE_REF"',
         'env': {'BASE_REF': '${{ github.event.pull_request.base.sha }}'}},
        {'run': 'databricks bundle validate --strict -t dev'},
        {'run': 'databricks bundle validate --strict -t prod'},
    ])
    pr['jobs']['migration']['if'] = 'github.event.pull_request.head.repo.full_name == github.repository'
    result = {'pr': pr}
    for name, target in (('integrate', 'dev'), ('promote', 'prod')):
        result[name] = workflow('Migration ' + name, {'workflow_dispatch': {'inputs': {
            'drops': {'description': 'Comma-separated drops, or all (estate order)', 'default': 'all', 'type': 'string'}}}}, target, [
                {'run': f'databricks bundle deploy -t {target}'},
                {'run': f'migration-ci integrate --target {target} --drops "$DROPS"', 'env': {'DROPS': '${{ inputs.drops }}'}},
            ])
        result[name]['concurrency'] = {'group': 'integrate' if name == 'integrate' else 'promote', 'cancel-in-progress': False}
    result['reset'] = workflow('Migration reset', {'workflow_dispatch': {'inputs': {
        'target': {'description': 'Target to destroy', 'type': 'choice', 'options': ['dev', 'prod'], 'required': True, 'default': 'dev'}}}}, '${{ inputs.target }}', [
            {'run': 'migration-ci reset --target "$TARGET"', 'env': {'TARGET': '${{ inputs.target }}'}},
        ])
    return result


def sync(target):
    target = Path(target).resolve()
    config = estate(target)
    source = Path(__file__).resolve().parent
    destination = target / '.migration/framework'
    if source == destination / 'migration_ci':
        raise ValueError('Run sync from the new framework distribution, not the destination copy')
    destination.mkdir(parents=True, exist_ok=True)
    (destination / '.gitignore').write_text('/build/\n*.egg-info/\n')
    if (destination / 'migration_ci').exists():
        shutil.rmtree(destination / 'migration_ci')
    shutil.copytree(source, destination / 'migration_ci', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(source / 'workflows', destination / 'workflows', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    source_root = source.parent
    current_version = (source_root / 'VERSION').read_text().strip() if (source_root / 'VERSION').exists() else version('migration-ci')
    (destination / 'VERSION').write_text(current_version + '\n')
    if (source_root / 'pyproject.toml').exists():
        shutil.copyfile(source_root / 'pyproject.toml', destination / 'pyproject.toml')
    else:
        (destination / 'pyproject.toml').write_text(
            '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
            f'[project]\nname = "migration-ci"\nversion = "{current_version}"\nrequires-python = ">=3.10"\n'
            'dependencies = ["PyYAML>=6,<7", "sqlglot>=26,<29", "duckdb>=1.2,<2", "databricks-sdk>=0.85,<1", "databricks-bundles==1.25.0"]\n'
            '[project.scripts]\nmigration-ci = "migration_ci.cli:main"\n[tool.setuptools]\npackages = ["migration_ci"]\n')
        with (destination / 'pyproject.toml').open('a') as stream:
            stream.write('[tool.setuptools.package-data]\nmigration_ci = ["workflows/*.py"]\n')
    (target / 'databricks.yml').write_text(yaml.safe_dump(bundle_config(config), sort_keys=False))
    (target / 'migration_resources.py').write_text(
        'import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent / ".migration/framework"))\n'
        'from migration_ci.bundle import load_resources\n')
    directory = target / '.github/workflows'
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in workflows().items():
        (directory / f'{name}.yml').write_text(yaml.safe_dump(data, sort_keys=False))
    requirements = target / 'requirements-dev.txt'
    content = requirements.read_text() if requirements.exists() else '.migration/framework\npytest>=8,<10\n'
    editable = ('-e .migration/framework', '--editable .migration/framework')
    requirements.write_text(''.join('.migration/framework\n' if line.strip() in editable else line
                                    for line in content.splitlines(keepends=True)))


def init(config_file, target):
    config_file, target = Path(config_file).resolve(), Path(target).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError('init target must be empty; use sync for existing estates')
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config_file, target / 'estate.yml')
    sync(target)
    (target / 'src/units').mkdir(parents=True)
    (target / 'tests').mkdir()
    (target / 'pytest.ini').write_text('[pytest]\ntestpaths = tests\n')
    (target / 'tests/conftest.py').write_text(
        'def pytest_addoption(parser):\n    parser.addoption("--goldens", default=None)\n')
    (target / 'tests/test_units.py').write_text(
        'from pathlib import Path\nfrom migration_ci.offline import check, summary\n\n'
        'def test_units(request):\n    goldens = request.config.getoption("--goldens")\n'
        '    rows = check(Path(__file__).resolve().parents[1], goldens=goldens, structural=goldens is None)\n'
        '    summary(rows)\n    assert not any(row["status"] == "FAIL" for row in rows)\n')
    (target / '.gitignore').write_text('.goldens/\n.databricks/\n.venv/\n__pycache__/\n*.egg-info/\n.pytest_cache/\n')
    (target / 'README.md').write_text('# ' + estate(target)['estate'] + '\n\n' + README)
