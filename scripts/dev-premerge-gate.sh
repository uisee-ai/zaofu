#!/usr/bin/env bash
# dev pre-merge 哨兵门(2026-07-04 立,基线红回潮防线)。
# 背景:07-03 基线红归零,07-04 一次 dev 合并即躺 13 红——多驱合并速度
# 下"谁发现谁修"追不上。合 dev 前必跑本门(<60s),红则不合。
# 哨兵集只挑"合并最易打红且秒级可跑"的合同类测试,不替代全量回归。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${ZF_PYTHON:-$(command -v python3)}"
if [ -x "/path/to/zaofu/.venv/bin/python" ]; then
  PY=/path/to/zaofu/.venv/bin/python
fi
exec env PYTHONPATH=src "$PY" -m pytest \
  tests/test_event_contracts.py \
  tests/test_registry_forcing_closure.py \
  tests/test_structure_discipline.py \
  tests/test_workflow_spine_projection.py \
  --no-cov -q "$@"
