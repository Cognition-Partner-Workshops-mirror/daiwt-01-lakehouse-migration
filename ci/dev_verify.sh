#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python -c 'import databricks.bundles' || { echo 'Install requirements-dev.txt in the active Python environment' >&2; exit 1; }
suffix=${1:?usage: ci/dev_verify.sh pr_<number> [units]}
[[ $suffix =~ ^pr_[0-9]+$ ]] || { echo "invalid PR suffix" >&2; exit 2; }
[[ -z $(git status --porcelain) ]] || { echo "worktree must be clean" >&2; exit 1; }
git fetch -q origin
[[ -n $(git branch -r --contains HEAD) ]] || { echo "HEAD must be on a remote branch" >&2; exit 1; }
head_sha=$(git rev-parse HEAD)
mkdir -p out
rm -f out/parity-*.json attestation.md
drops=(D0)
parity_files=()
for D in "${drops[@]}"; do parity_files+=("out/parity-$D.json"); done
: > out/run-urls.txt
echo '[]' > out/selected-units.json
selection=changed
if [[ ${2:-} == '*' ]]; then selection='*'; elif [[ -n ${2:-} ]]; then selection=explicit; fi
status=PASS
if ! python - "$suffix" "${2:-}" > out/selected-units.json <<'PY'
import json, sys
sys.path.insert(0, "ci")
from select_units import select, required
from registry import load
print('Required set: ' + json.dumps(required(sys.argv[1])), file=sys.stderr)
print(json.dumps(select(load(), sys.argv[1], sys.argv[2])))
PY
then
  echo '[]' > out/selected-units.json
  status=FAIL
fi
echo "Selected units: $(cat out/selected-units.json)"
if [[ $status == PASS ]]; then
  if ! databricks bundle validate --strict -t dev --var "schema_suffix=$suffix" ||
     ! databricks bundle deploy -t dev --var "schema_suffix=$suffix"; then status=FAIL; fi
fi
if [[ $status == PASS ]]; then
  units=$(python -c 'import json; print(",".join(json.load(open("out/selected-units.json"))))')
  if [[ -n $units ]]; then
    if ! python ci/cleanup.py --suffix "$suffix" --units "$units"; then status=FAIL; fi
  fi
fi
if [[ $status == PASS ]]; then
  for D in "${drops[@]}"; do
    if ! python ci/run_units.py "$suffix" out/selected-units.json --drop "$D" --run-urls out/run-urls.txt ||
       ! databricks bundle run -t dev --var "schema_suffix=$suffix" verification --params "drop=$D,units=${units//,/:}" ||
       ! python src/parity/parity_check.py --suffix "$suffix" --drop "$D" --units out/selected-units.json --output "out/parity-$D.json"; then
      status=FAIL
      break
    fi
  done
fi
if ! python ci/attest.py "${parity_files[@]}"; then status=FAIL; fi
if ! python ci/record_attestation.py --head-sha "$head_sha" --suffix "$suffix" --status "$status" \
  --units out/selected-units.json --selection "$selection"; then status=FAIL; fi
[[ $status == PASS ]]
