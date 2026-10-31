> 状态: active

# P1：日志轮转后的消费游标丢事件

来源：2026-10-30 codebase review；用户已授权新建 worktree，验证存在后执行修复。
基线：`3746d313`。

## 复现证据

新活动文件大小等于或超过旧 offset 时漏读；重启也漏掉归档未消费尾部。

`PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_review_consistency_regressions.py -q --no-cov --tb=short --basetemp /tmp/zf-review-consistency-20261031T0058/red`

结果：7 failed / 1 passed；本项对应 `test_watcher_reads_equal_or_larger_rotated_segment / test_restart_consumes_unread_archive_tail_then_new_active`。

## 范围与验收

消费位置绑定文件身份，轮转时读取归档余量和后续 segment；将身份与 session offset 原子持久化。

1. 原始 HEAD 复现 -> verify: 上述回归断言失败，且 idle 对照通过。
2. 修复当前状态/游标读取 -> verify: 同一回归用例转绿，补充边界和重启覆盖。
3. 验证调用闭包 -> verify: 聚焦 pytest、确定性 mock E2E、dev-premerge-gate 通过。
4. 实现与归档 -> verify: 记录实现 commit 与验证结果，本地提交，不 push。

## 执行记录

新增整数兼容的 EventOffset，绑定 device/inode，跨归档补读尾部；SessionStore 原子持久化 byte offset 和身份，watcher 等长轮转也交付批次。

- 聚焦闭包：158 passed；回归、直接调用方与终态 mock E2E 全部通过。
- 自动验证：13,541 passed / 16 failed / 20 skipped；16 项均在干净基线 `3746d313`
  复现，失败集合完全一致，不声明全量绿色。
- serial：10 passed；premerge sentinel：51 passed；flow smoke：10 passed。
- Web typecheck / Web unit、改动 Python 文件 Ruff、git diff whitespace 检查通过。
- 复现、命令、分层结果与基线失败分类见
  [实现和验证记录](../../docs/impl/review-state-consistency.md)。
- 实现 commit 在归档时记录；本地提交，不 push。
