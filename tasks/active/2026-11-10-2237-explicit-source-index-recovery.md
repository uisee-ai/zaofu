# 受控增量恢复保留显式 source_index

> 状态: active

根代理已授权在独立 worktree 修复并提交，由根代理唯一合并；不操作活动 LS 或其冻结候选。

## 已核问题与范围

dev `4c446537` 的 candidate-rework-apply 已验证并固定显式 source_index，但随后 gap writer 仍递归改写 payload.source_index_ref。LS 候选复现历史 parent OLD→NEW、重复 source_refs 被去重；Package inline producer 与 writer loader/TaskContract 顶层 ref 可指向不同正文。证据位于 LS records 的 `source-index-explicit-producer-rewrite-probe.json`，本任务仅用独立测试夹具重现。

仅修改 `control_actions_candidate_rework`：使用已由 materialize_explicit_plan_ports 验证的显式 source_index 原 ref；无显式 producer 时保留 legacy 派生。非法 descriptor 继续拒绝，身份、审批、矩阵及来源校验不放松。product_delivery task-local required 覆盖问题属于另一入口，不在本次源码范围。

## 验收

1. 先复现显式正文再次改写 -> verify: 真实 controlled action 到 task_map.ready 测试在旧代码失败，历史 parent 和重复来源差异可见。
2. 精确传播已固定 ref -> verify: controlled action → ready → load_writer_task_map → canonical TaskContract 回读，正文与显式输入逐值相等、历史 parent/重复 source 保留。
3. 拒绝无效显式声明 -> verify: 缺 ref/摘要、文件缺失、摘要不匹配、重复/畸形 descriptor 无 ready/amended 事件，无 legacy fallback。
4. 兼容原路径 -> verify: 不提供显式 producer 时仍走原 source_index 派生；不声明 source_index 时不新增该正文。
5. 影响闭包与交付 -> verify: dev-verify plan/run、相关 deterministic mock E2E、premerge sentinel；更新既有 impl 文档，提交实现，再以实现 hash 归档任务。

## 实施与验证

`control_actions_candidate_rework` 仅从已固定的显式端口取 source_index ref，跳过该正文的自动再派生；ready/amended 及真实 writer 的 source_index_ref 指向相同正文。原无显式 producer 的派生路径保留。全部测试使用临时独立状态，无活动 LS 写入。

- 新回归先在 dev `4c446537` 复现 **4 failed / 8 passed**；修复后 `tests/test_control_actions_candidate_rework_source_index.py` **12 passed**，含真实 controlled action → ready → loader → Orchestrator canonical materialization → TaskStore 回读。
- `python scripts/dev-verify.py plan --base dev`：55 个文件，覆盖直接调用者与 orchestration 边界；用项目共享 `.venv/bin/python scripts/dev-verify.py run --base dev` 执行 **1002 passed、9 skipped**（45.76s），自动 premerge sentinel **53 passed**（3.18s），mock flow smoke **10 passed**（2.21s）。未运行 host/real_provider。
- 补充直接调用闭包：`test_gap_task_input_delivery.py`、`test_gap_explicit_replacement.py`、`test_module_gap_plan.py`、`test_module_gap_plan_runtime.py`、`test_plan_artifact_package.py`、`test_writer_fanout_runtime.py`、`test_sidecar_refs.py`、`test_plan_artifact_ports.py`，`pytest -q --no-cov -m 'not perf and not host and not real_provider'`：**273 passed**（66.06s）。
- `git diff --check` 与本次文件秘密模式扫描通过。机械检查不声称 LS 最终业务验收已完成；实际 LS Package 及 loaded 引用仍由根代理在合并后核验。
