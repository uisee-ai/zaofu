"""Orchestrator-agent (Layer 2) briefing assembler.

Different from worker briefings: orchestrator agent operates on the WHOLE
system (all features, all tasks, all roles), not on a single task. So the
briefing is system-wide and includes:

  1. Trigger event (the event that woke the agent up)
  2. Feature list (current state)
  3. Kanban (current state)
  4. Recent events (last N)
  5. Memory (shared + orchestrator role memory)
  6. Git state (optional)
  7. Available tools reminder

Layer 1 generates this on every dispatch to the orchestrator agent. Layer 2
reads it via the briefing_path passed in the prompt.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.store import FeatureStore
from zf.core.memory.store import MemoryStore
from zf.core.metrics.collector import MetricsCollector, MetricsSnapshot
from zf.core.task.store import TaskStore
from zf.runtime.fast_path import render_fast_path_policy


def build_orchestrator_briefing(
    *,
    state_dir: Path,
    config: ZfConfig,
    trigger_event: ZfEvent,
    recent_events_limit: int = 30,
    also_triggered: list[str] | tuple[str, ...] = (),
) -> str:
    sections: list[str] = []
    sections.append(f"# Orchestrator wake — {trigger_event.ts}")
    sections.append("")
    # doc 66 §14.0/§14.4: when a burst coalesced into this turn, list the other
    # trigger event types so Layer 2 knows this wake covers more than one signal
    # (state below is rebuilt from disk and already reflects all of them).
    if also_triggered:
        shown = list(also_triggered)[:20]
        more = len(also_triggered) - len(shown)
        suffix = f" (+{more} more)" if more > 0 else ""
        sections.append(
            f"> This wake coalesced **{len(also_triggered) + 1}** triggers. "
            f"Primary: `{trigger_event.type}`; also: "
            f"{', '.join(f'`{t}`' for t in shown)}{suffix}."
        )
        sections.append("")
    sections.append("You are the **orchestrator agent**. You decide what to do next based on")
    sections.append("the state below. You have a deterministic session_id, so you remember your")
    sections.append("previous decisions automatically (via Claude Code --resume).")
    sections.append("")

    sections.append("## Trigger")
    sections.append(_render_trigger(trigger_event))
    sections.append("")

    triage_contract = _render_rework_triage_contract(trigger_event)
    if triage_contract:
        sections.append("## Rework Semantic Triage Contract")
        sections.append(triage_contract)
        sections.append("")

    sections.append("## Features")
    sections.append(_render_features(state_dir))
    sections.append("")

    sections.append("## Kanban")
    sections.append(_render_kanban(state_dir))
    sections.append("")

    sections.append("## Recent Events")
    sections.append(_render_events(state_dir, recent_events_limit))
    sections.append("")

    sections.append("## Memory")
    sections.append(_render_memory(state_dir))
    sections.append("")

    # LH-1.T5: MetricsSnapshot summary. Always present (gives Layer 2 a
    # baseline each wake); uses ⚠ when thresholds breached.
    sections.append("## Current Health")
    sections.append(_render_health(state_dir))
    sections.append("")

    sections.append("## Runtime Context")
    sections.append(f"- Runtime state dir: `{state_dir}`")
    sections.append("- Worker/provider processes receive `ZF_STATE_DIR`; if you run `zf` commands manually from a detached workdir, keep using this runtime state dir.")
    sections.append("- Use the runtime state dir for temporary payload files; do not create a sibling `.zf` tree in the worker workdir.")
    sections.append("")

    fast_path_policy = render_fast_path_policy(config.workflow.fast_path)
    if fast_path_policy:
        sections.append(fast_path_policy)
        sections.append("")

    # LH-1.T3: Drift warnings. Zero-noise: section only appears when
    # there were drift events in the last hour.
    drift_txt = _render_drift(state_dir)
    if drift_txt:
        sections.append("## Drift Warnings")
        sections.append(drift_txt)
        sections.append("")

    # LH-1.T4: Promoted rules (learned constraints). Zero-noise too.
    rules_txt = _render_promoted_rules(state_dir)
    if rules_txt:
        sections.append("## Learned Constraints")
        sections.append(rules_txt)
        sections.append("")

    sections.append("## Available Tools")
    sections.append(_render_tools(config))
    sections.append("")

    sections.append("## What to do")
    sections.append("Based on the state above, decide ONE thing:")
    sections.extend(_render_user_message_intake_rules(config))
    sections.append("  Create features with `feature_id=$(zf feature add \"Feature title\" --id-only)`")
    sections.append("  or `zf feature add \"Feature title\" --json`; do not parse human-readable output.")
    sections.append("  Create linked tasks with `task_id=$(zf kanban add \"$feature_id\" \"Task title\" --id-only)`")
    sections.append("  or `task_id=$(zf kanban add \"Task title\" --feature \"$feature_id\" --id-only)`; do not parse human-readable output.")
    initial_guidance = _render_initial_dispatch_guidance(config)
    if initial_guidance:
        sections.append(f"  {initial_guidance}")
    sections.append("- If the trigger is `arch.proposal.done`: kernel auto-routes to critic; no orchestrator action needed.")
    sections.extend(_render_design_approved_rules(config))
    sections.append("- If the trigger is `design.critique.done` with verdict=reject (or `gate.failed`): dispatch arch")
    sections.append("  for rework. Include critic's `fix_items` in the next briefing so arch v2 addresses them.")
    handoff_events = _workflow_handoff_events(config)
    if handoff_events:
        sections.append(f"- If the trigger is {_format_event_list(handoff_events)}: kernel auto-routes;")
        sections.append("  no orchestrator action unless the chain stalls.")
    sections.append("- If the trigger is a failure (`*.rejected` / `*.failed` / `*.blocked`): decide rework, escalate, or change scope.")
    sections.append("- If the trigger is `human.escalate`: this is a meta-event about a stall, NOT a fresh failure.")
    sections.append("  Read the escalation payload (reason, origin/task id), find the original failure, and emit ONE")
    sections.append("  concrete follow-up action THIS turn: `critic.gate.requested` for ambiguous triage,")
    sections.append("  `task.contract.update` to reduce scope, `task.cancel` on retry exhaustion, or")
    sections.append("  `worker.respawn.requested` when the worker is offline. See skill")
    sections.append("  `zf-yoke-orchestrator-role-context` for the routing table. Do NOT wait for a human steer.")
    sections.append("- If state is consistent and nothing actionable: emit `orchestrator.idle` and exit")
    sections.append("")
    sections.append("Make ONE round of decisions via tool calls, then exit.")
    sections.append("Do NOT keep working in a loop — Layer 1 will call you again on the next event.")
    sections.append("")
    sections.append("## Stage ④ backlog 合成 — design.critique.done verdict=approve 后必须执行")
    sections.append("")
    if _has_arch_intake(config):
        sections.append("**关键纪律**: 千万不要在 intake 阶段就写 implementation contract。intake 只创 Feature + 第一个 design task (role=arch)。")
        sections.append("arch 可以产出 draft/proposed 的 plan/backlog/task-map artifact,但这些只是候选输入。Contract 的合成发生在**这里** — critic 审通过之后, 由 orchestrator 接受、合并或重写成最终 kanban contract。")
    else:
        sections.append("**关键纪律**: 本拓扑没有 `arch` intake;不要照搬 design-first 流程。user.message 可直接创建带最终 contract 的实现任务,再派给可用实现角色。")
        sections.append("如果本拓扑也没有 `design.critique.done`,本节仅作为设计门拓扑参考,不要等待不存在的 critic 事件。")
    sections.append("")
    sections.append("**必填 6 个 required_backlog_refs** (写进 task.contract 的字段):")
    sections.append("")
    sections.append("| 字段 | 数据源 |")
    sections.append("|---|---|")
    sections.append("| `spec_ref`         | arch/orchestrator artifact manifest 中 spec/sdd 路径；最终 contract 必须引用已审核可用版本 |")
    sections.append("| `plan_ref`         | orchestrator 最终接受的 plan/process_plan 路径；可由 arch draft/proposed 候选合并而来 |")
    sections.append("| `tdd_ref`          | 已审核可用的 tdd/test_plan 路径或紧凑验证策略 |")
    sections.append("| `critic_event_id`  | design.critique.done.id |")
    sections.append("| `critic_gate_ref`  | verdict + fix_items 摘要 |")
    sections.append("| `evidence_contract`| arch + critic 一致的 acceptance / verification schema |")
    sections.append("")
    sections.append(
        "**Artifact-first 约束**: backlog synthesis 必须优先消费 "
        "`task.artifact_refs.updated` / `artifact.manifest.published` 中的 "
        "draft/proposed/accepted artifact refs、hash 状态和 critic verdict；不要把 `arch.proposal.done:<event_id>` "
        "或聊天 transcript 当成唯一 plan_ref。若缺 "
        "manifest/event refs,先要求 arch/critic 补发 manifest。若候选 artifact 已被 "
        "critic approve,你可以接受原件、合并成新的最终 artifact,或拆成 task-map；"
        "只有 orchestrator 写入的 `task.contract.update` 才是下游交付 contract。"
    )
    sections.append("")
    sections.extend(_render_backlog_synthesis_commands(config))
    sections.append("")
    sections.append("**详细流程**: see skill `zf-harness-backlog-synthesis` for the full procedure with examples.")
    sections.append("")
    sections.append("## 派发协议 (关键, 别跳过)")
    sections.append("")
    sections.append("把一个任务交给 worker 只需要**两步**:")
    sections.append("")
    sections.append("```bash")
    sections.append('state_tmp="${ZF_STATE_DIR:-.zf}/tmp"')
    sections.append("task_id=$(zf kanban add \"$feature_id\" \"Task title\" --id-only)")
    sections.append('zf emit task.contract.update --task "$task_id" --payload-file "$state_tmp/contract.json"')
    sections.append("zf kanban assign \"$task_id\" <role-name>   # 传 role.name (例如 dev / arch / review), 不要传 instance_id")
    sections.append("```")
    sections.append("")
    sections.append("复杂 JSON payload 必须用 Python 标准库生成到文件,再用 `--payload-file`; 不要依赖 `jq`、Node 或其他可能未安装的外部工具。")
    sections.append("推荐模式:")
    sections.append("")
    sections.append("```bash")
    sections.append('state_tmp="${ZF_STATE_DIR:-.zf}/tmp"')
    sections.append('mkdir -p "$state_tmp"')
    sections.append("STATE_TMP=\"$state_tmp\" python3 - <<'PY'")
    sections.append("import json")
    sections.append("import os")
    sections.append("from pathlib import Path")
    sections.append("payload = {\"contract\": {\"behavior\": \"...\", \"verification\": \"...\", \"verification_tiers\": [\"runtime\"], \"owner_role\": \"<role-name>\", \"scope\": [\"src/file.py\"]}}")
    sections.append("state_tmp = Path(os.environ['STATE_TMP'])")
    sections.append("state_tmp.mkdir(parents=True, exist_ok=True)")
    sections.append("(state_tmp / 'contract.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')")
    sections.append("PY")
    sections.append('zf emit task.contract.update --task "$task_id" --payload-file "$state_tmp/contract.json"')
    sections.append("```")
    sections.append("")
    sections.append("strict contract 必填字段:")
    sections.append("- `behavior`: 最终要交付的行为,不是 arch/review/test 这个阶段动作本身。")
    sections.append("- `verification`: 可执行验证命令优先,例如 `python3 -m pytest tests/test_x.py`; 不要写成纯 prose。")
    sections.append("- `verification_tiers`: 至少一个,只能用 `static`, `runtime`, `e2e`, `manual_evidence`。")
    sections.append("  例: pytest/脚本用 `runtime`; compile/typecheck 用 `static`; browser/API flow 用 `e2e`。")
    sections.append("- `owner_role` 或 `owner_instance`: 一般填目标 role.name,必须来自本项目 zf.yaml。")
    sections.append("- `scope`: 只能是最终交付文件的 raw relative paths,如 `[\"src/greet.py\", \"tests/test_greet.py\"]`; 不要写 prose,不要用 include/exclude 描述文字。")
    sections.append("- `acceptance`, `exclusions`, `wave`, `shared_files`, `exclusive_files`, `handoff_artifacts` 按任务实际范围填写。")
    sections.append("缺少 `verification_tiers` 等 strict 字段会触发 `task.contract.invalid`,Layer 1 不会派发。")
    if initial_guidance:
        sections.append(initial_guidance)
    sections.append("")
    sections.append("创建任务给脚本变量时必须用 `--id-only` 或 `--json`; 不要在命令替换里 `printf` / `tee` 人类可读输出,")
    sections.append("否则变量会变成多行文本并污染 `task.contract.update --task`。")
    sections.append("")
    sections.append("**`<role-name>` 用 role.name 就够了** — Layer 1 dispatcher 会在该 role 的 replicas")
    sections.append("里按 WIP 自动挑空闲实例 (1 忙 1 闲必选闲; 全忙 defer 到下一轮). 只有在你确实需要")
    sections.append("强制路由到某个具体 replica 时才传 instance_id. 默认用 role.name,")
    sections.append("让 Layer 1 做负载均衡 — 见下文「可派发 worker」表查看每个 role 的 replicas / backend.")
    sections.append("")
    sections.append("**做完这两步就停**。Layer 1 watcher 下一 cycle 会:")
    sections.append("1. 看到 `task.assigned_to` 被设")
    sections.append("2. 自动把任务状态推进到 `in_progress`")
    sections.append("3. 把 task briefing send-keys 到对应 worker 的 tmux pane")
    sections.append("")
    sections.append("**绝对不要自己**运行 `zf kanban move TASK-XXX in_progress` —")
    sections.append("这会让任务跳过 Layer 1 的派发队列 (`task_store.ready()` 只看 backlog),")
    sections.append("worker 永远收不到 briefing, 系统陷入静默死锁。")
    sections.append("")
    sections.append("## 流程阶段协议 (基于本项目 zf.yaml 动态派生)")
    sections.append("")
    sections.append("收到 worker 完成事件后, **必须**按本项目 YAML 声明的拓扑派给下一角色,")
    sections.append("**不要自己 move 任务状态**, 也**不要提前 close feature**.")
    sections.append("")
    workflow_desc = _render_workflow_from_topology(config)
    if workflow_desc:
        sections.append(workflow_desc)
    else:
        sections.append("(YAML 未声明 triggers/publishes 边, 无法推导拓扑 — 先修 zf.yaml,不要套用默认 dev/review/test 流程.)")
        sections.append("")
    roster_desc = _render_worker_roster(config)
    if roster_desc:
        sections.append(roster_desc)
    # Terminal-done rule is derived from the topology: if a role publishes
    # judge.passed/judge.failed, that role is the terminal claim. Otherwise
    # the last subscriber in the chain is the terminal claim. In all cases,
    # deterministic Layer 1 owns the actual done transition after evidence
    # and discriminator checks pass.
    terminal_rule = _render_terminal_rule(config)
    sections.append("**硬规则**:")
    sections.append("- 每个事件只能触发本表列出的下一站, 不要自己 move 状态跳过阶段")
    sections.append("- 失败事件 (review.rejected/verify.failed/test.failed/judge.failed) 必须显式 rework:")
    sections.append("  `zf kanban assign <task-id> <rework-role>` (`<rework-role>` 从 task.contract.rework_to / workflow.rework_routing / zf.yaml 推导)")
    sections.append("- 不要为 rework 构造复杂 JSON payload; rework 的最小正确动作就是 assign 回目标 role")
    sections.append(f"- {terminal_rule}")
    sections.append("- 不要手动 close feature; feature completion 由 Layer 1 在所有 task done 后投影。")
    sections.append("")
    sections.append("## 完成协议 (Run 6 new contract)")
    sections.append("")
    sections.append("做完本轮所有决策后,**必须**运行:")
    sections.append("")
    sections.append("```bash")
    sections.append("zf emit orchestrator.round.complete")
    sections.append("```")
    sections.append("")
    sections.append("这是 Layer 1 kernel 知道你这一轮结束的唯一信号。没发出这个事件,")
    sections.append("kernel 会认为你还在思考 — 下一个 trigger 事件到来时不会再唤醒你。")
    sections.append("")
    sections.append("如果中途遇到不可恢复错误(如 `zf kanban add` 报错、权限不足),emit:")
    sections.append("")
    sections.append("```bash")
    sections.append("zf emit orchestrator.dispatch_failed --payload '{\"reason\": \"<描述>\"}'")
    sections.append("```")
    sections.append("")
    sections.append("然后同样再 emit `orchestrator.round.complete` 结束本轮。")
    return "\n".join(sections)


def _has_arch_intake(config: ZfConfig) -> bool:
    return any(
        role.name == "arch" and "task.assigned" in role.triggers
        for role in config.roles
    )


def _resolved_role_kind(role: object) -> str:
    """Local mirror of loader role_kind resolution for briefing text."""
    kind = str(getattr(role, "role_kind", "auto") or "auto")
    if kind != "auto":
        return kind
    if getattr(role, "name", "") in {"review", "test", "judge", "verify", "critic"}:
        return "reader"
    return "writer"


def _implementation_role_names(config: ZfConfig) -> list[str]:
    """Return roles that can receive implementation/work-product dispatch.

    `arch` is intentionally excluded even though it may be a writer in the
    broad sense: it writes plans, not the post-critic implementation contract.
    """
    roles = [
        role for role in config.roles
        if role.name not in {"orchestrator", "arch"}
    ]
    staged = [
        role.name for role in roles
        if "implement" in {str(stage) for stage in (role.stages or [])}
    ]
    if staged:
        return list(dict.fromkeys(staged))
    writers = [
        role.name for role in roles
        if _resolved_role_kind(role) == "writer"
        and ("task.assigned" in (role.triggers or []) or role.triggers)
    ]
    return list(dict.fromkeys(writers))


def _primary_implementation_role(config: ZfConfig) -> str:
    roles = _implementation_role_names(config)
    return roles[0] if roles else ""


def _format_event_list(events: list[str]) -> str:
    return " / ".join(f"`{event}`" for event in events)


def _workflow_handoff_events(config: ZfConfig) -> list[str]:
    """List configured progress events that Layer 1 can route mechanically."""
    candidates = [
        "dev.build.done",
        "static_gate.passed",
        "static_gate.skipped",
        "review.approved",
        "verify.passed",
        "test.passed",
        "judge.passed",
    ]
    published: set[str] = set()
    for role in config.roles:
        published.update(role.publishes or [])
    return [event for event in candidates if event in published]


def _render_design_approved_rules(config: ZfConfig) -> list[str]:
    target = _primary_implementation_role(config)
    if target:
        roles = _implementation_role_names(config)
        target_text = f"`{target}`" if len(roles) == 1 else _format_event_list(roles)
        return [
            "- If the trigger is `design.critique.done` **with verdict=approve**: **backlog stage** — synthesize",
            f"  the final task contract from arch+critic outputs, then dispatch according to zf.yaml (primary target: {target_text}). See dedicated section below.",
        ]
    return [
        "- If the trigger is `design.critique.done` **with verdict=approve**: **backlog stage** — synthesize",
        "  and validate plan/backlog artifacts. This topology has no implementation writer role, so do not dispatch dev/review/test/judge.",
    ]


def _render_backlog_synthesis_commands(config: ZfConfig) -> list[str]:
    target = _primary_implementation_role(config)
    if not target:
        return [
            "**Plan-only 合成命令** (本拓扑没有 implementation writer role):",
            "",
            "```bash",
            "# 1) 确认 critic 已 approve 的 candidate plan/backlog artifact 已落盘并带 frontmatter",
            "#    如需合并/规范化,先写入 orchestrator-final artifact,再 validate。",
            "plan_path=\"docs/plans/<approved-or-final-plan-artifact>.md\"",
            "zf spec validate --strict \"$plan_path\"",
            "zf spec ingest --dry-run \"$plan_path\"",
            "",
            "# 2) 如需登记 artifact refs,用事件记录 refs,但不要派发实现角色",
            'state_tmp="${ZF_STATE_DIR:-.zf}/tmp"',
            'zf emit task.artifact_refs.updated --task "$task_id" --payload-file "$state_tmp/artifact-refs.json"',
            "",
            "# 3) 本轮决策结束; 不要 assign dev/review/test/judge",
            "zf emit orchestrator.round.complete",
            "```",
        ]

    return [
        f"**合成命令** (目标实现角色来自 zf.yaml: `{target}`; 用 Python 标准库生成 contract.json,**不要**用 jq):",
        "",
        "```bash",
        'state_tmp="${ZF_STATE_DIR:-.zf}/tmp"',
        "STATE_TMP=\"$state_tmp\" python3 - <<'PY'",
        "import json",
        "import os",
        "from pathlib import Path",
        "contract = {",
        "    \"contract\": {",
        "        # 从 critic-approved candidate artifact refs + arch/critic 摘要合成:",
        "        \"behavior\": \"<arch.summary>\",",
        "        \"scope\": [\"<arch.file_plan[0]>\", \"<arch.file_plan[1]>\", \"...\"],",
        "        \"verification\": \"<arch.test_plan 转可执行命令>\",",
        "        \"verification_tiers\": [\"runtime\"],",
        "        \"acceptance\": \"<arch acceptance + critic.fix_items 转条款>\",",
        "        \"exclusions\": [\"<critic.risks 衍生的禁止>\"],",
        f"        \"owner_role\": \"{target}\",",
        "        \"handoff_artifacts\": [\"<expected output files>\"],",
        "        # 6 个 required_backlog_refs:",
        "        \"spec_ref\": \"docs/specs/phase-1/...\",",
        "        \"plan_ref\": \"docs/plans/<approved-or-final-plan-artifact>.md\",",
        "        \"tdd_ref\": \"<test_plan summary>\",",
        "        \"critic_event_id\": \"<design.critique.done.id>\",",
        "        \"critic_gate_ref\": \"approve: <fix_items summary>\",",
        "        \"evidence_contract\": {\"static\": \"...\", \"runtime\": \"...\"},",
        "    }",
        "}",
        "state_tmp = Path(os.environ['STATE_TMP'])",
        "state_tmp.mkdir(parents=True, exist_ok=True)",
        "(state_tmp / 'contract.json').write_text(json.dumps(contract, ensure_ascii=False), encoding='utf-8')",
        "PY",
        'zf emit task.contract.update --task "$task_id" --payload-file "$state_tmp/contract.json"',
        f"zf kanban assign \"$task_id\" {target}",
        "```",
        "",
        f"**可选 fanout** (scope 大可拆 N 个并发 `{target}` task,避同包 package.json 撞车):",
        "",
        "```bash",
        'state_tmp="${ZF_STATE_DIR:-.zf}/tmp"',
        "# scope 按包/文件切分:",
        "sub_task=$(zf kanban add \"$feature_id\" \"Subtask: file_A\" --id-only)",
        "# 给每个 sub_task 写自己子集的 contract (scope 不重叠,共享 6 refs)",
        'zf emit task.contract.update --task "$sub_task" --payload-file "$state_tmp/contract-sub.json"',
        f"zf kanban assign \"$sub_task\" {target}",
        "```",
    ]


def _initial_intake_role(config: ZfConfig) -> str:
    """Pick the first role a fresh user-message task should target."""
    if _has_arch_intake(config):
        return "arch"
    for preferred in ("dev", "implementer", "worker"):
        for role in config.roles:
            if role.name == preferred and "task.assigned" in role.triggers:
                return role.name
    for role in config.roles:
        if role.name != "orchestrator" and "task.assigned" in role.triggers:
            return role.name
    for role in config.roles:
        if role.name != "orchestrator":
            return role.name
    return "<role-name>"


def _render_user_message_intake_rules(config: ZfConfig) -> list[str]:
    """Render topology-aware user.message intake instructions."""
    initial_role = _initial_intake_role(config)
    if initial_role == "arch":
        return [
            "- If the trigger is a `user.message`: **intake stage** — create a Feature + first design task",
            "  (role=arch, NO contract yet). Kernel will auto-route arch → critic.",
        ]
    return [
        "- If the trigger is a `user.message`: **intake stage** — create a Feature + first implementation task",
        f"  for `{initial_role}` with a final-deliverable contract before assignment.",
        "  This topology has no `arch` intake role; do not assign `arch` or wait for arch/critic events.",
    ]


def _render_initial_dispatch_guidance(config: ZfConfig) -> str:
    """Tell Layer 2 which role should receive a fresh user-message task."""
    has_arch_intake = any(
        role.name == "arch" and "task.assigned" in role.triggers
        for role in config.roles
    )
    has_critic = any(
        role.name == "critic" and "arch.proposal.done" in role.triggers
        for role in config.roles
    )
    if has_arch_intake and has_critic:
        target = _primary_implementation_role(config)
        target_text = (
            f"`{target}`"
            if target
            else "plan/backlog artifacts only (no implementation writer role)"
        )
        return (
            "本拓扑启用了 arch/critic 设计门: user.message 创建 task 后先 "
            "`zf kanban assign \"$task_id\" arch`,不要绕过设计门直接派发下游角色; "
            "contract.owner_role 填初始 owner `arch`,但 contract.behavior / "
            "verification / scope 必须描述最终实现交付,不是 arch 阶段活动; "
            "设计通过 `design.critique.done` 后由 orchestrator 进入 backlog 阶段, "
            f"再按 zf.yaml 选择下一步: {target_text}。"
        )
    if has_arch_intake:
        return (
            "本拓扑启用了 arch intake: user.message 创建 task 后先 "
            "`zf kanban assign \"$task_id\" arch`,不要绕过 intake 直接派发下游角色。"
        )
    initial_role = _initial_intake_role(config)
    return (
        f"本拓扑没有 arch intake: user.message 创建 task 后直接写最终交付 contract,再 "
        f"`zf kanban assign \"$task_id\" {initial_role}`; 不要 assign arch,也不要等待不存在的 "
        "arch/critic 事件。"
    )


def _render_trigger(event: ZfEvent) -> str:
    lines = [
        f"- **Type**: `{event.type}`",
        f"- **Actor**: `{event.actor or '?'}`",
        f"- **Task**: `{event.task_id or '?'}`",
        f"- **Timestamp**: {event.ts}",
    ]
    if event.payload:
        lines.append(f"- **Payload**: `{event.payload}`")
    return "\n".join(lines)


def _render_rework_triage_contract(event: ZfEvent) -> str:
    if event.type != "orchestrator.rework.triage.requested":
        return ""
    payload = event.payload if isinstance(event.payload, dict) else {}
    request_id = str(payload.get("request_id") or "")
    task_id = str(event.task_id or payload.get("task_id") or "")
    fingerprint = str(payload.get("failure_fingerprint") or "")
    failure_count = int(payload.get("failure_count") or 0)
    recovery_context = payload.get("recovery_context_ref")
    recovery_context_ref = (
        str(recovery_context.get("ref") or "")
        if isinstance(recovery_context, dict)
        else str(recovery_context or "")
    )
    advice = {
        "schema_version": "orchestrator.rework-triage.advice.v1",
        "request_id": request_id,
        "task_id": task_id,
        "failure_fingerprint": fingerprint,
        "recommended_action": "REPLACE_WITH_ONE_ALLOWED_VALUE",
        "guidance": "REPLACE_WITH_CONCISE_ACTIONABLE_GUIDANCE",
        "evidence_event_ids": payload.get("failure_event_ids") or [],
        "apply_policy": "proposal_only",
    }
    return "\n".join([
        "This is proposal-only semantic triage requested by Run Manager.",
        "Do not dispatch, reassign, edit TaskStore, emit `task_map.ready`, or emit "
        "`orchestrator.replan_requested`.",
        f"Read task `{task_id}`, failure fingerprint `{fingerprint}`, and all "
        f"{failure_count} evidence events listed in the trigger payload.",
        (
            f"Read the bounded recovery context sidecar `{recovery_context_ref}` before deciding."
            if recovery_context_ref
            else "The trigger has no recovery context sidecar; use the listed evidence events only."
        ),
        "Choose exactly one `recommended_action`: `continue_rework`, "
        "`precise_rework`, `revise_contract`, `split_task`, `replan`, "
        "`diagnose`, or `human`.",
        "Then emit exactly one advisory event with concise evidence-based guidance:",
        "```bash",
        "zf emit orchestrator.rework.triage.recorded "
        f"--task {json.dumps(task_id)} --actor orchestrator --payload "
        f"{json.dumps(json.dumps(advice, ensure_ascii=False))}",
        "```",
        "Run Manager validates and applies the advice; your turn ends after this event.",
    ])


def _render_workflow_from_topology(config: ZfConfig) -> str:
    """P2-2 (2026-04-20): replace hardcoded 4-stage table with a
    description derived from WorkflowTopology. This way the briefing
    matches whatever team shape the YAML declared — 3-role code-assist,
    5-role design-first, safe-team, or custom."""
    from zf.core.workflow.topology import WorkflowTopology
    topology = WorkflowTopology.from_config(config)
    return topology.to_workflow_description()


def _render_worker_roster(config: ZfConfig) -> str:
    """B-MULTIREPLICA-02 / B-MIXEDBACKEND-01 (2026-04-23): surface the
    actual roster Layer 1 will route against.

    Grouping rule: when every replica of a role shares the same backend
    (the common case), show one "N replicas, backend=X" summary line so
    the briefing stays terse. When replicas disagree (mixed pool, e.g.
    dev-1 claude + dev-2 codex), list each instance on its own line so
    Layer 2 can see which instance_id ↔ backend pair exists and pick
    deliberately (or still just pass role.name for Layer 1 to fallback).
    """
    # Group instances by role.name, keeping the per-instance backend.
    by_role: dict[str, list[tuple[str, str]]] = {}
    for role in config.roles:
        if role.name == "orchestrator":
            continue
        by_role.setdefault(role.name, []).append(
            (role.instance_id, role.backend)
        )
    if not by_role:
        return ""
    example_role = next(iter(sorted(by_role)))
    lines = ["## 可派发的 worker (role / instance_id / backend)", ""]
    for name in sorted(by_role):
        instances = by_role[name]
        backends = {b for _, b in instances}
        if len(backends) == 1:
            # Homogeneous pool — one-line summary.
            (backend,) = backends
            slot = "1 replica" if len(instances) == 1 else f"{len(instances)} replicas"
            lines.append(f"- `{name}` — {slot}, backend=`{backend}`")
        else:
            # Mixed-backend pool — one line per instance so Layer 2 can
            # see which instance_id runs which backend.
            lines.append(f"- `{name}` — mixed backend pool ({len(instances)} replicas):")
            for instance_id, backend in instances:
                lines.append(f"  - `{instance_id}` — backend=`{backend}`")
    lines.append("")
    lines.append(f"**派发时传 role.name 就好** (如 `zf kanban assign TASK-XXX {example_role}`),")
    lines.append("Layer 1 会自动在 replicas 里挑 WIP 可用的实例. 只有需要强制路由")
    lines.append("到特定 backend (例如混合池里 claude vs codex 的 A/B 对比) 时才传")
    lines.append("具体 instance_id.")
    lines.append("")
    return "\n".join(lines)


def _render_terminal_rule(config: ZfConfig) -> str:
    """Decide which event terminates a task based on YAML topology.

    Priority:
      judge.passed (if a judge role exists)
      > verify.passed (if a verify role exists)
      > test.passed (if a test role exists)
      > review.approved (if a review role exists)
      > first terminal event (no subscribers) matching *.passed / *.approved

    This unblocks the LH-3 bug class where code-assist.yaml (no judge)
    made Layer 2 wait forever for judge.passed.
    """
    all_publishes: set[str] = set()
    for role in config.roles:
        for event in role.publishes:
            all_publishes.add(event)

    # Preference order reflects current production convention
    for candidate in ("judge.passed", "verify.passed", "test.passed", "review.approved"):
        if candidate in all_publishes:
            return (
                f"`{candidate}` 是 terminal claim;不要手动 "
                "`zf kanban move <task-id> done`,Layer 1 会在 "
                "discriminator/evidence 通过后机械关闭任务。"
            )

    # Fallback: any terminal .passed / .approved event
    for event in sorted(all_publishes):
        if event.endswith((".passed", ".approved")):
            return (
                f"`{event}` 是 terminal claim;不要手动 "
                "`zf kanban move <task-id> done`,Layer 1 会在 "
                "discriminator/evidence 通过后机械关闭任务。"
            )

    return ("YAML 未声明 terminal-gate 事件, 请根据任务 contract 自行判断何时 done")


def _render_features(state_dir: Path) -> str:
    path = state_dir / "feature_list.json"
    if not path.exists():
        return "_(no features yet)_"
    fs = FeatureStore(path)
    features = fs.list_all()
    if not features:
        return "_(no features yet)_"
    return "\n".join(
        f"- `{f.id}` [{f.status}] p{f.priority} — {f.title}"
        for f in features
    )


def _render_kanban(state_dir: Path) -> str:
    path = state_dir / "kanban.json"
    if not path.exists():
        return "_(no tasks)_"
    ts = TaskStore(path)
    tasks = ts.list_all()
    if not tasks:
        return "_(no tasks)_"
    # R-TASK-STATE-AXIS-01 (2026-04-27): show derived lifecycle phase
    # alongside status so Layer 2 can distinguish "in_progress + just
    # finished build" from "in_progress + just got reviewed". Phase is
    # derived from the most recent stage event in events.jsonl, not
    # stored on Task — see core/task/lifecycle.py.
    from zf.core.events.log import EventLog
    from zf.core.task.lifecycle import derive_phase

    events_path = state_dir / "events.jsonl"
    events: list = []
    if events_path.exists():
        try:
            events = list(EventLog(events_path).read_days(1))
        except Exception:
            events = []
    lines = []
    for t in tasks:
        assignee = f"@{t.assigned_to}" if t.assigned_to else "(unassigned)"
        phase = derive_phase(t, events) if events else None
        phase_tag = f" phase={phase}" if phase else ""
        lines.append(
            f"- `{t.id}` [{t.status}{phase_tag}] {assignee} — {t.title}"
        )
    return "\n".join(lines)


def _render_events(state_dir: Path, limit: int) -> str:
    path = state_dir / "events.jsonl"
    if not path.exists():
        return "_(no events)_"
    log = EventLog(path)
    events = log.read_all()[-limit:]
    if not events:
        return "_(no events)_"
    return "\n".join(
        f"- `{e.ts}` `{e.type}` actor=`{e.actor or '?'}` task=`{e.task_id or '?'}`"
        for e in events
    )


def _render_memory(state_dir: Path) -> str:
    memory_dir = state_dir / "memory"
    if not memory_dir.exists():
        return "_(no memory)_"
    store = MemoryStore(memory_dir)
    parts: list[str] = []
    shared = store.get(None)
    if shared:
        parts.append("**Shared:**")
        parts.extend(f"- [{e.type}] {e.content}" for e in shared)
    role_mem = store.get("orchestrator")
    if role_mem:
        parts.append("**Orchestrator-specific:**")
        parts.extend(f"- [{e.type}] {e.content}" for e in role_mem)
    return "\n".join(parts) if parts else "_(no memory entries)_"


_DRIFT_WINDOW_SECONDS = 3600.0
_PROMOTED_RULES_MAX_AGE_SECONDS = 7 * 24 * 3600.0


def _render_health(state_dir: Path) -> str:
    """LH-1.T5: compact 4-line health summary (one per metric group).

    Emits ⚠ for any metric that crossed alert thresholds so Layer 2 can
    react without reading the full snapshot.
    """
    try:
        snap = _compute_snapshot(state_dir)
    except Exception as exc:
        return f"_(metrics unavailable: {exc})_"
    lines = [
        f"- **A 持续性**: MTTS={snap.mtts:.1f}  "
        f"StuckRecov={snap.stuck_recovery_rate:.2f}  "
        f"CrashFreeH={snap.crash_free_hours:.2f}",
        f"- **B 对齐**: VCR={snap.vcr:.2f}  "
        f"Scope={snap.scope_violation_rate:.2f}  "
        f"GoalDrift={snap.goal_drift:.2f}",
        f"- **C 进度**: Throughput/h={snap.throughput_per_hour:.2f}  "
        f"Rework={snap.rework_ratio:.2f}  Done={snap.tasks_done}",
        f"- **D 经济**: Cost/Task=${snap.cost_per_task:.4f}  "
        f"Tokens/Task={snap.token_per_task:.0f}  "
        f"BudgetBreach={snap.budget_breach_rate:.2f}",
    ]
    if snap.alerts:
        lines.append("")
        lines.append("⚠ Alerts:")
        for a in snap.alerts:
            lines.append(f"  - {a}")
    else:
        lines.append("")
        lines.append("_(no alerts)_")
    return "\n".join(lines)


def _compute_snapshot(state_dir: Path) -> MetricsSnapshot:
    events = EventLog(state_dir / "events.jsonl")
    tasks = TaskStore(state_dir / "kanban.json")
    cost = CostTracker(state_dir / "cost.jsonl")
    return MetricsCollector.compute(events=events, tasks=tasks, cost=cost)


def _render_drift(state_dir: Path) -> str:
    """LH-1.T3: list recent worker.drift.detected events (last hour).

    Returns "" (empty) when there's nothing to show — the caller omits
    the section entirely to avoid prompt noise.
    """
    path = state_dir / "events.jsonl"
    if not path.exists():
        return ""
    log = EventLog(path)
    try:
        events = log.read_all()
    except Exception:
        return ""
    cutoff = time.time() - _DRIFT_WINDOW_SECONDS
    entries: list[str] = []
    from datetime import datetime
    for e in events[-200:]:
        if e.type != "worker.drift.detected":
            continue
        try:
            ts = datetime.fromisoformat(e.ts).timestamp()
        except Exception:
            ts = time.time()
        if ts < cutoff:
            continue
        payload = e.payload or {}
        signal = payload.get("signal", "unknown")
        severity = payload.get("severity", "?")
        detail = (payload.get("detail") or "")[:140]
        entries.append(
            f"- `{signal}` (severity {severity}) — {detail}"
        )
    return "\n".join(entries) if entries else ""


def _render_promoted_rules(state_dir: Path) -> str:
    """LH-1.T4: show active, fresh promoted rules (filter > 7-day stale)."""
    path = state_dir / "promoted_rules.jsonl"
    if not path.exists():
        return ""
    cutoff = time.time() - _PROMOTED_RULES_MAX_AGE_SECONDS
    lines: list[str] = []
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            r = json.loads(raw)
        except Exception:
            continue
        try:
            ts = float(r.get("promoted_at", 0))
        except (TypeError, ValueError):
            ts = 0.0
        if ts < cutoff:
            continue
        cat = r.get("category", "?")
        rule = r.get("rule", "")
        occ = r.get("occurrences", 1)
        lines.append(f"- [{cat}] `{rule}` (seen {occ}×)")
    return "\n".join(lines) if lines else ""


def _render_tools(config: ZfConfig) -> str:
    orch = next((r for r in config.roles if r.name == "orchestrator"), None)
    if orch is None or not orch.allowed_tools:
        return "_(no tools allowlisted; check your zf.yaml)_"
    return "\n".join(f"- `{t}`" for t in orch.allowed_tools)
