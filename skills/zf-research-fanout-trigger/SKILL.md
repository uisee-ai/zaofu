---
name: zf-research-fanout-trigger
description: "ZaoFu project-level trigger for starting the fixed-role research fanout workflow from a channel or Kanban Agent request. Use when the user explicitly invokes this skill, asks to trigger/start/run research fanout, wants a channel/Kanban Agent research request turned into a fixed multi-role fanout, or wants research results prepared for PRD/refactor prompt generation. Do not use for generic web searching or ordinary channel discussion that has not requested research fanout."
---

# ZaoFu Research Fanout Trigger

## Objective

Turn an explicit channel or Kanban Agent research request into a ZaoFu
controlled workflow trigger for the fixed-role research fanout template.

This skill is the entrypoint. The runtime fanout and subagents do the research.

## Ground Rules

- Treat this as a ZaoFu project skill, not a user-global research helper.
- Read `AGENTS.md` if you need repository rules or are about to run commands.
- Use `zf.yaml` as the only control-plane config. Do not hard-code `.zf`.
- Do not write `events.jsonl`, `kanban.json`, `session.yaml`, or other runtime
  truth files directly.
- Trigger through a controlled action, preferably `workflow-invoke`.
- Do not silently run research from ordinary channel text. Require explicit
  skill invocation or an explicit user request for research fanout.
- If a required field, token, task, or workflow stage is missing, produce a
  preview payload and the exact blocker instead of bypassing the gate.

## Fixed Template

Use this template unless the user explicitly names another ZaoFu research
template:

```yaml
template_id: research-fanout.fixed.v1
pattern_id: research-fanout
roles:
  - source_researcher
  - product_analyst
  - technical_analyst
  - risk_critic
  - synthesizer
outputs:
  - research_summary
  - evidence_refs
  - open_questions
  - prd_prompt_input
  - refactor_prompt_input
```

Role intent:

- `source_researcher`: gather and cite primary or direct evidence.
- `product_analyst`: convert findings into user needs, scope, and PRD inputs.
- `technical_analyst`: identify architecture, implementation, and integration
  implications.
- `risk_critic`: challenge assumptions, missing evidence, rollout risk, and
  failure modes.
- `synthesizer`: produce the final research synthesis and PRD/refactor prompt
  inputs.

Runtime mapping: the first four roles run as `fanout.children`; `synthesizer`
runs as `aggregate.synth_role` after the child reports complete.

## Trigger Workflow

1. Extract the request:
   - `topic`: the concrete thing to research.
   - `scope`: bounded aspects to investigate.
   - `expected_output`: default to
     `research synthesis plus PRD/refactor prompt inputs`.
   - `channel_id` and `thread_id`: preserve the originating conversation when
     available.
   - `task_id`: use the current task when present. If no task exists, create a
     tracking task through the controlled `create-task` action or stop and ask
     for a task id.

2. Validate the workflow stage:
   - Default `pattern_id` is `research-fanout`.
   - Confirm `zf.yaml` declares that stage/pattern before executing
     `workflow-invoke`.
   - If missing, do not invent runtime truth. Return the preview payload and
     say the `research-fanout` stage must be added to `zf.yaml`.

3. Build the controlled action payload:

```json
{
  "task_id": "TASK-ID",
  "pattern_id": "research-fanout",
  "channel_id": "ch-example",
  "thread_id": "main",
  "requested_by": "skill:zf-research-fanout-trigger",
  "reason": "explicit research fanout request from channel/Kanban Agent",
  "scope": [
    "topic: <research topic>",
    "template: research-fanout.fixed.v1"
  ],
  "expected_output": "research synthesis plus PRD/refactor prompt inputs",
  "risk": "cost-bearing multi-agent research; keep evidence and open questions explicit",
  "source_refs": {
    "template_id": "research-fanout.fixed.v1",
    "trigger_surface": "channel_or_kanban_agent",
    "channel_id": "ch-example",
    "thread_id": "main"
  },
  "artifact_refs": []
}
```

4. Execute through the Web controlled action when available:

```bash
curl -sS -X POST "http://127.0.0.1:8001/api/actions/workflow-invoke" \
  -H "Content-Type: application/json" \
  -H "X-ZF-Web-Token: ${ZF_WEB_ACTION_TOKEN}" \
  --data @/tmp/zf-research-fanout-payload.json
```

If no Web action token or dashboard is available, return the payload and the
command rather than using raw event writes.

5. Expected runtime sequence:

```text
skill trigger
-> /api/actions/workflow-invoke
-> workflow.invoke.requested
-> workflow.invoke.accepted
-> task.fanout.requested / fanout.requested
-> fixed-role research workers
-> fanout.aggregate.completed
-> channel.state_update.posted(status=research_completed)
-> channel discussion/synthesis
-> PRD/refactor workflow prompt package
```

## Channel Reporting

After triggering, report these fields to the channel or user:

- `status`: requested, preview_only, or blocked.
- `pattern_id` and `template_id`.
- `task_id`, `channel_id`, and `thread_id`.
- `workflow_run_id`, `workflow_input_manifest_ref`, and
  `workflow_prompt_ref` when the action returns them.
- Any blocker, especially missing `task_id`, missing `research-fanout` stage,
  missing action token, or dashboard unavailable.

## Refusals

Do not execute if:

- the request is only a generic "research this" without explicit fanout intent;
- no task id exists and task creation is not authorized;
- `zf.yaml` does not declare the requested pattern/stage;
- the only possible path is direct mutation of runtime truth files;
- the user asks to skip gates, fabricate evidence, or hide cost/risk.
