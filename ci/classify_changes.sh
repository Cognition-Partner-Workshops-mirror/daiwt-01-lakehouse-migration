#!/usr/bin/env bash
set -euo pipefail
python ci/classify_changes.py "${BASE_SHA:-origin/main}" "${HEAD_SHA:-HEAD}"
