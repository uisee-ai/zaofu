> 状态: done
> 实现: `ceed9e72 fix: preserve event consumption and current task state`

# P1：任务再次归档读取过期合同

来源：2026-10-30 codebase review；用户已授权新建 worktree，验证存在后执行修复。
基线：`3746d313`。

## 复现证据

同日 done → replan 重开 → done 后，get 返回第一版；terminal index 丢失时同样错误。

`PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_review_consistency_regressions.py -q --no-cov --tb=short --basetemp /tmp/zf-review-consistency-20261031T0058/red`

结果：7 failed / 1 passed；本项对应 `test_reclosed_task_returns_latest_contract_and_can_reopen_again`。

## 范围与验收

归档 get 优先最新记录，保留历史审计记录，并验证第三次合同 CAS 重开。

1. 原始 HEAD 复现 -> verify: 上述回归断言失败，且 idle 对照通过。
2. 修复当前状态/游标读取 -> verify: 同一回归用例转绿，补充边界和重启覆盖。
3. 验证调用闭包 -> verify: 聚焦 pytest、确定性 mock E2E、dev-premerge-gate 通过。
4. 实现与归档 -> verify: 记录实现 commit 与验证结果，本地提交，不 push。

## 执行记录

TaskStore.get 对指定归档及缺失索引的回退按最新日期/记录逆序查找，保留历史记录并恢复重复 replan 的合同 CAS。

- 聚焦闭包：158 passed；回归、直接调用方与终态 mock E2E 全部通过。
- 自动验证：13,541 passed / 16 failed / 20 skipped；16 项均在干净基线 `3746d313`
  复现，失败集合完全一致，不声明全量绿色。
- serial：10 passed；premerge sentinel：51 passed；flow smoke：10 passed。
- Web typecheck / Web unit、改动 Python 文件 Ruff、git diff whitespace 检查通过。
- 复现、命令、分层结果与基线失败分类见
  [实现和验证记录](../../docs/impl/review-state-consistency.md)。
- 实现 commit：`ceed9e72 fix: preserve event consumption and current task state`；本地提交，不 push。
