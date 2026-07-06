#!/bin/bash
# E4:三流最小 smoke(kernel/workflow/profile 改动后的固定回归入口)。
set -e
cd "$(dirname "$0")/.."
PYTHONPATH=src python -m pytest \
  tests/test_flow_smoke_e2e.py \
  tests/test_controller_flow_smoke_matrix.py \
  -q -p no:cacheprovider "$@"
