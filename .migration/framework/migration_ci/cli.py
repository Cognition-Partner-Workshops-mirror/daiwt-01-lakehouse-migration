import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from .goldens import fetch
from .model import discover, estate, schema_name, select
from .offline import check, summary
from .scaffold import init, sync


def warehouse(config, statement):
    from databricks.sdk import WorkspaceClient
    client = WorkspaceClient()
    response = client.statement_execution.execute_statement(
        warehouse_id=config['warehouse_id'], statement=statement, wait_timeout='50s')
    while response.status.state.value in ('PENDING', 'RUNNING'):
        time.sleep(1)
        response = client.statement_execution.get_statement(response.statement_id)
    if response.status.state.value != 'SUCCEEDED':
        raise RuntimeError(str(response.status.error))
    if response.result:
        print(response.result.data_array)
    return response


def integrate(config, target, drops, schema_suffix=''):
    verification = schema_name('verification', schema_suffix)
    requested = config['drops'] if drops == 'all' else drops.split(',')
    if not requested or set(requested) - set(config['drops']):
        raise ValueError('Unknown drops')
    for drop in config['drops']:
        if drop in requested:
            subprocess.run(['databricks', 'bundle', 'run', '-t', target, '--var', f'schema_suffix={schema_suffix}', 'migration', '--params', f'drop={drop}'], check=True)
    warehouse(config, f'SELECT run_id, drop, status, count(*) AS tables FROM `{config[target + "_catalog"]}`.`{verification}`.parity_results GROUP BY ALL ORDER BY run_id, drop, status')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='migration-ci')
    commands = parser.add_subparsers(dest='command', required=True)
    cmd = commands.add_parser('init')
    cmd.add_argument('estate_file')
    cmd.add_argument('target_dir')
    commands.add_parser('sync').add_argument('target_dir')
    for name in ('check', 'fetch-goldens'):
        cmd = commands.add_parser(name)
        cmd.add_argument('--units')
        cmd.add_argument('--changed-from')
        if name == 'check':
            cmd.add_argument('--goldens')
    cmd = commands.add_parser('integrate')
    cmd.add_argument('--target', choices=['dev', 'prod'], required=True)
    cmd.add_argument('--drops', default='all')
    cmd.add_argument('--schema-suffix', default=os.environ.get('BUNDLE_VAR_schema_suffix', ''))
    cmd = commands.add_parser('reset')
    cmd.add_argument('--target', choices=['dev', 'prod'], required=True)
    cmd.add_argument('--schema-suffix', default=os.environ.get('BUNDLE_VAR_schema_suffix', ''))
    for action in ('land', 'parity'):
        cmd = commands.add_parser(action)
        cmd.add_argument('--catalog', required=True)
        cmd.add_argument('--drop', required=True)
        cmd.add_argument('--run-id', default='manual')
        cmd.add_argument('--schema-suffix', default='')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            init(args.estate_file, args.target_dir)
        elif args.command == 'sync':
            sync(args.target_dir)
        elif args.command == 'check':
            results = check(goldens=args.goldens, names=args.units.split(',') if args.units else None, changed_from=args.changed_from)
            summary(results)
            return int(any(r['status'] == 'FAIL' for r in results))
        elif args.command == 'fetch-goldens':
            config = estate('.')
            units, _, _ = discover('.')
            selected = select('.', units, args.units.split(',') if args.units else None, args.changed_from)
            print(f'Downloaded/verified {fetch(".", config, units, selected)} golden files')
        elif args.command in ('land', 'parity'):
            from .runtime import run
            run(args.command, Path.cwd(), args.catalog, args.drop, args.run_id, args.schema_suffix)
        else:
            config = estate('.')
            if args.command == 'integrate':
                integrate(config, args.target, args.drops, args.schema_suffix)
            else:
                schema_name('verification', args.schema_suffix)
                subprocess.run(['databricks', 'bundle', 'destroy', '-t', args.target, '--var', f'schema_suffix={args.schema_suffix}', '--auto-approve'], check=True)
                catalog = config[args.target + '_catalog']
                for schema in ('bronze', 'silver', 'gold', 'verification'):
                    warehouse(config, f'DROP SCHEMA IF EXISTS `{catalog}`.`{schema_name(schema, args.schema_suffix)}` CASCADE')
        return 0
    except Exception as exc:
        if args.command == 'check':
            summary([{'unit': '*', 'status': 'FAIL', 'detail': str(exc)}])
        else:
            print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
