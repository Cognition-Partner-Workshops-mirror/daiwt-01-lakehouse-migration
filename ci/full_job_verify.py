"""Run the complete nightly job and record pushed-head full-chain evidence."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/parity'))
from full_job_check import require_complete_registry
from registry import load
from run_units import deployed_jobs


def run_job(suffix, key, run_urls, params=None):
    jobs = deployed_jobs('dev', suffix)
    if not jobs.get(key, {}).get('id'):
        raise ValueError('Job absent from bundle summary: ' + key)
    command = ['databricks', 'bundle', 'run', '-t', 'dev', '--var', f'schema_suffix={suffix}']
    if params:
        command += ['--params', params]
    command.append(key)
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        for line in process.stdout:
            print(line, end='', flush=True)
            if 'Run URL' in line:
                with run_urls.open('a') as output:
                    output.write(line.strip() + '\n')
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, command)


def verify(suffix):
    if not re.fullmatch(r'pr_[0-9]+', suffix):
        raise ValueError('Full verification requires pr_<number>')
    if subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip():
        raise ValueError('Worktree must be clean')
    subprocess.run(['git', 'fetch', '-q', 'origin'], check=True)
    if not subprocess.check_output(['git', 'branch', '-r', '--contains', 'HEAD'], text=True).strip():
        raise ValueError('HEAD must be on a remote branch')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    registry = load(ROOT)
    out = ROOT / 'out'
    out.mkdir(exist_ok=True)
    units = out / 'selected-units.json'
    units.write_text(json.dumps(sorted(registry)))
    urls = out / 'run-urls.txt'
    urls.write_text('')
    result = out / 'parity-D0.json'
    result.unlink(missing_ok=True)
    status = 'PASS'
    try:
        require_complete_registry(registry, json.loads((ROOT / 'src/parity/contract.json').read_text()))
        for action in ('validate', 'deploy'):
            command = ['databricks', 'bundle', action, '-t', 'dev', '--var', f'schema_suffix={suffix}']
            if action == 'validate':
                command.append('--strict')
            subprocess.run(command, check=True)
        run_job(suffix, 'nvb_nightly', urls, 'drop=D0')
        run_job(suffix, 'full_job_verification', urls)
        subprocess.run([sys.executable, 'src/parity/full_job_check.py', '--suffix', suffix,
                        '--output', str(result)], check=True)
    except Exception as error:
        print(f'Full job verification failed: {error}', file=sys.stderr)
        status = 'FAIL'
    if subprocess.run([sys.executable, 'ci/attest.py', str(result)]).returncode:
        status = 'FAIL'
    recorded = subprocess.run([sys.executable, 'ci/record_attestation.py', '--head-sha', head,
                               '--suffix', suffix, '--status', status, '--units', str(units),
                               '--selection', 'full-job'])
    return 0 if status == 'PASS' and recorded.returncode == 0 else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('suffix')
    args = parser.parse_args()
    return verify(args.suffix)


if __name__ == '__main__':
    raise SystemExit(main())
