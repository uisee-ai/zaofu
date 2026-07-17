#!/usr/bin/env bash
# LH-6.T4 legacy guard: reject known rogue parallel control stores.
#
# This does not prove EventLog-only rebuild or classify canonical ZaoFu stores.
# Fail if a known undeclared store appears (state.json / truth.json / world.json).
# Exit 0 when clean, 1 on violation.
set -euo pipefail

STATE_DIR="${1:-.zf}"

for rogue in "$STATE_DIR/state.json" "$STATE_DIR/truth.json" \
             "$STATE_DIR/world.json"; do
  if [ -f "$rogue" ]; then
    echo "invariant_single_truth: rogue truth store $rogue" >&2
    exit 1
  fi
done
exit 0
