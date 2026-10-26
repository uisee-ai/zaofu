# 全面审查与重构准备

只有明确要求 full review、全面扫描或重构计划时使用。
先完成架构地图，再根据实际目录构造 Review Prompt V2；继续深查和覆盖审计，
不要只交 bootstrap。按缺口生成 supplement，最终区分 confirmed / inferred /
uncovered / known defer。报告和任务切片在回复中给出；写文件需要当前任务授权。

## 历史范围

先用 `git log -30 --oneline` 和目标路径的 `git log -20 -- <path>` 找到变更与 owner。
需要判定历史兼容或架构漂移时，明确相关时间段或 commit range 后再扩大。
只有用户要求全历史考古或缺陷溯源确有需要，才读取 `git log --all`；
没有足够提交的公开导出不能假定 `HEAD~30` 存在。
输出简短 History Map：相关演进、热点、漂移、仍缺失的历史证据。

### Step 8 — Coverage Audit

After the deep review, run a coverage audit before synthesis. The audit is
fail-closed: uncovered areas stay explicit and must not be hidden in a
"complete" conclusion.

Minimum coverage matrix:

```json
{
  "subsystem": "runtime/orchestration",
  "expected_paths": ["src/zf/runtime/", "tests/test_star_topology.py"],
  "inspected_paths": ["src/zf/runtime/orchestrator.py"],
  "evidence_refs": ["src/zf/runtime/orchestrator.py:Orchestrator.run_once"],
  "coverage": "partial",
  "uncovered": ["writer fanout resume route"],
  "followup_prompt": "Review writer fanout resume route..."
}
```

Coverage categories:

- `git_history_drift`
- `control_plane`
- `state_event_truth`
- `runtime_orchestration`
- `star_fanout`
- `skills_materialization`
- `workspace_project`
- `integrations_web_api`
- `kanban_agent_channels`
- `autoresearch_eval_loop`
- `provider_headless_streaming`
- `security_permissions`
- `observability_diagnostics`
- `performance_cost_context`
- `compatibility_examples_runbooks`
- `tests_docs_backlogs`

### Step 9 — Supplement Review

If coverage audit finds meaningful gaps, generate a supplement prompt targeted
only at the missing evidence. Do not rerun broad review unless the repo map was
wrong.

Supplement prompt must include:

- exact uncovered category
- expected paths to inspect
- invariant or behavior to verify
- required evidence shape
- stop condition


## 收敛与交付

有意义的缺口只补查目标路径；工具/时间限制下明确未覆盖，不承诺全覆盖。
输出：系统地图、核心不变量、按严重度的问题、coverage matrix、测试缺口、
可选 refactor slices（allowed_paths / 依赖 / gate / 验证方法）、剩余疑问。
没有证据支持缺陷时允许“本次范围未发现”；不要为凑任务数制造建议。

只有用户明确要求并行审查才拆分只读子任务；每个切片交还具体文件证据和未覆盖项。
