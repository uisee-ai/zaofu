> 状态: active

# P2：Web 指标被旧归档覆盖

来源：2026-10-30 codebase review；用户已授权新建 worktree，验证存在后执行修复。
基线：`3746d313`。

## 复现证据

TaskStore 当前为 backlog，metric_task 却是 active=1/status=done。

`PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_review_consistency_regressions.py -q --no-cov --tb=short --basetemp /tmp/zf-review-consistency-20261031T0058/red`

结果：7 failed / 1 passed；本项对应 `test_web_metrics_keep_reopened_task_current`。

## 范围与验收

指标投影先归并最新归档，再由活跃记录覆盖，保留当前任务唯一事实。

1. 原始 HEAD 复现 -> verify: 上述回归断言失败，且 idle 对照通过。
2. 修复当前状态/游标读取 -> verify: 同一回归用例转绿，补充边界和重启覆盖。
3. 验证调用闭包 -> verify: 聚焦 pytest、确定性 mock E2E、dev-premerge-gate 通过。
4. 实现与归档 -> verify: 记录实现 commit 与验证结果，本地提交，不 push。

## 执行记录

Web metric_task 按 ID 合并当前记录，活跃任务覆盖归档，新增指纹版本使既有投影缓存刷新。

- 聚焦闭包：158 passed；回归、直接调用方与终态 mock E2E 全部通过。
- 自动验证：13,541 passed / 16 failed / 20 skipped；16 项均在干净基线 `3746d313`
  复现，失败集合完全一致，不声明全量绿色。
- serial：10 passed；premerge sentinel：51 passed；flow smoke：10 passed。
- Web typecheck / Web unit、改动 Python 文件 Ruff、git diff whitespace 检查通过。
- 复现、命令、分层结果与基线失败分类见
  [实现和验证记录](../../docs/impl/review-state-consistency.md)。
- 实现 commit 在归档时记录；本地提交，不 push。
