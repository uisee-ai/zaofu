# 事件消费与任务当前态一致性修复

2026-10-31，基线 `3746d313`。在独立 worktree 对 codebase review 的 4 项 P1/P2
建立文件级及真实 Orchestrator 回归，修复前为 7 failed / 1 passed。

## 事件读取

[Watcher 批次合同](watcher-batch-wake.md) 保留批次顺序与不可变上界。运行时确认游标
前补齐此前未唤醒批次；跨轮转的 offset 绑定 segment 身份，重启从 Session 恢复。
不修改事件格式、业务状态迁移或 Agent 的权限。缺少身份的旧游标保留兼容语义。

## 任务查询和指标

TaskStore 继续保留每次终态归档用于审计。`get()` 在索引指定日期内逆序查找；索引
缺失时按日期和记录逆序回退，使同日多次 replan/结束后的合同 revision 仍可进行下一次
CAS 更新。活跃记录始终优先；`list_all_with_archive()` 仍提供完整历史，不改变其 API。

Web metric_task 按 ID 取最后的归档记录，再由活跃记录覆盖。这样 backlog 重开任务
计入 ready 列，不被旧 done 记录覆盖，也不会重复计数。指标指纹带 `metric-tasks.v2`，
使已存在的可重建缓存在下一次刷新时失效；没有修改 canonical Task 或归档正文。

## 验证入口

- `tests/test_review_consistency_regressions.py`：观察→唤醒→idle、先 idle 的对照、
  等长/更大新 segment、跨轮转重启、同日重复归档/缺失索引、Web 活跃状态优先。
- `tests/test_event_offset.py`：真实按日/大小轮转、签名解码、归档中的批次上界、
  多消费者独立位置及 Session 安全 YAML 往返。
- `tests/e2e/test_terminal_kanban_convergence_mock_e2e.py`：终态结算与重启。
- `scripts/dev-verify.py`：影响闭包、Web 检查、哨兵与 deterministic mock flow smoke。

任务记录分别为 `tasks/active/2026-10-31-0058-{watcher-batch-gap,event-rotation-cursor,
task-archive-currentness,web-reopened-task-metrics}.md`，完成后归档到 `tasks/done/`。

## 2026-10-31 历史验证证据与基线限制

环境：复用主 checkout 的 `.venv`，`PYTHONPATH=src` 指向当前 worktree，并将该 venv 的
`bin` 放到 PATH 首位，确保 shell 子进程也使用同一依赖集。未调用真实 provider。

最终聚焦闭包执行以下测试文件，结果 **158 passed**：

```sh
python -m pytest tests/test_event_offset.py tests/test_review_consistency_regressions.py \
  tests/test_events_archive.py tests/test_events_log.py tests/test_runtime_watcher.py \
  tests/test_cli_watch_batch.py tests/test_watched_batch.py tests/test_task_terminal_archive.py \
  tests/test_web_metric_facts.py tests/test_session_store.py tests/test_orchestrator_lifecycle_handlers.py \
  tests/test_task_contract_authority_service.py tests/test_workflow_spine_projection.py \
  tests/test_cli_watch.py tests/e2e/test_terminal_kanban_convergence_mock_e2e.py -q --no-cov
```

扩大检查使用 `python scripts/dev-verify.py plan --base dev --workers 4` 和
`python scripts/dev-verify.py run --base dev --workers 4 --keep-going`。
EventLog / Store / Session / Runtime 共享边界触发自动 deterministic 套件；排除
`serial`、`perf`、`host`、`real_provider`，serial 另跑，不能当作真实 provider E2E。

首轮扩大检查为 13,537 passed / 20 failed / 20 skipped。行数哨兵的本次失败已通过将
批次读取放入现有 EventCursorMixin 修复；3 项环境失败来自继承到嵌套 pytest 的
`PYTEST_ADDOPTS=--basetemp=...` 以及 PATH 未使用 venv，调整后通过。

对这 20 个失败 nodeid 在干净基线 worktree（`3746d313`）和修复 worktree 分别重跑，
两边都为 **16 failed / 4 passed**，失败集合完全一致：

| 基线问题 | 数量 | 入口 |
|---|---:|---|
| plan-approve fixture 已不处于 awaiting review | 5 | `test_feishu_callback_security.py`、`test_feishu_callback_token.py`、`test_feishu_consume.py` |
| fixture artifact ref 不符合 clean relative path | 5 | `test_evidence_review_194.py`、`e2e/test_generic_workflow_complex_mock_e2e.py` |
| tracked checkout 缺少 export-public.sh / workflow prompt | 5 | `test_export_public.py`、`test_prompt_source_closure.py` |
| bounded dispatch fixture 未产生 stage.dispatched | 1 | `e2e/test_oa_bounded_dispatch_mock_e2e.py` |

以上是本批四项修复以外的基线问题；没有修改测试预期或跳过这些断言。
自动扩大套件仍有既有失败，不声明全量绿色。

最终自动复跑：**13,541 passed / 16 failed / 20 skipped**；失败集合与干净基线完全一致。
serial **10 passed**，premerge sentinel **51 passed**（manual docs currentness OK），
flow smoke **10 passed**；Web typecheck、Web unit、改动 Python 文件 Ruff 与 diff whitespace
检查均通过。扩大回归仍返回失败状态，原因仅为上表的既有问题。

范围审查：16 个变更文件，跨 EventLog / Session / TaskStore / Runtime / Web projection
共享边界，按 high 风险进行扩大验证；未更改 canonical 状态文件、凭据、provider 或配置。
原 checkout 的未跟踪 `sima/` 保持不变。本分支仅本地提交，交接后冻结，未合并或 push。

## 2026-11-17 当前 dev 整合

以 `194552ca` 为基线重跑一致性回归，得到 5 failed / 3 passed，确认事件轮转、
观察批次补读和重开任务指标仍需修复。TaskStore 最新归档查询已由 `b1f028e3` 落地，
本轮保留现实现及回归，不重复替换。上节的全量结果仅代表 10 月 31 日历史版本。

`7ee5611e fix: preserve event cursors and current task projections` 整合事件消费与投影，
补齐同日碰撞的独立 segment、丢失游标历史时显式失败、Watcher 回调失败后重试。
`6422418f fix: retain recovery evidence and release interrupted writers` 整合中断 writer
释放、集成失败检查点与反馈保留。旧 source-index 测试在当前 dev 已有更强断言，
保留当前版本。恢复合同见 [长期运行恢复](185-long-horizon-run-recovery.md)。

验证分两组执行：恢复路径 6 个文件 **349 passed**；EventLog/Session、真实 watcher/
CLI/runtime 调用、TaskStore/Web 投影和 mock replay 的 34 个文件 **493 passed**。
两组均通过 `pytest -q --no-cov -n 4 --dist loadfile` 执行，排除 host、perf、
real_provider；serial 单独 **10 passed**。Web typecheck/unit、Ruff 和 diff 检查通过；
premerge sentinel **53 passed**，flow smoke **10 passed**。

使用 `scripts/dev-verify.py plan --base dev --workers 4` 检查自动影响闭包。
共享 EventLog/Orchestrator import 扇出会把 800 多个文件提升为整仓 deterministic
步骤；按仓库聚焦验证规则，用上述显式闭包替代该步骤，执行
`scripts/dev-verify.py run --base dev --workers 4 --skip-tier deterministic` 完成其他层。
本轮未执行全量 pytest，也不将 mock E2E 当作真实 provider 交付证明。
