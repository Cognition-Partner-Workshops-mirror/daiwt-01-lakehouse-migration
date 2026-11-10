#!/usr/bin/env bash
set -euo pipefail
case ${1:-} in
  dev) python ci/cleanup.py --all ;;
  prod)
    databricks bundle destroy -t prod --auto-approve
    python ci/cleanup.py --prod
    ;;
  *) echo "usage: ci/reset.sh dev|prod" >&2; exit 2 ;;
esac
