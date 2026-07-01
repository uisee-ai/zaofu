#!/usr/bin/env bash
# Run the ZaoFu robustness suite from the repository root.
#
# Default:
#   tests/e2e/scripts/run_robustness_suite.sh
#
# Real provider stress:
#   tests/e2e/scripts/run_robustness_suite.sh --include-real mixed --confirm-real

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
PYTHONPATH=src python3 -m tests.e2e.robustness_suite "$@"
