---
name: zf-workflow-start-planner
description: "Use when a ZaoFu operator, Kanban Agent, Channel member, or Coding Agent must classify a tracked Task, clarify missing workflow inputs, recommend one active workflow route, or create a durable workflow-start proposal through zf CLI. Do not use to bypass approval, invent workflow stages, or write runtime truth."
---

# ZaoFu Workflow Start Planner

## Objective

Turn one existing ZaoFu Task into an evidence-backed workflow route proposal.
The skill owns semantic recommendation. ZaoFu code owns route availability,
Task/config binding, proposal identity, approval, idempotency, and execution.

## Boundary

- Read `zf.yaml` only through the project and `zf workflow routes`; do not
  maintain a second route list.
- Never write `events.jsonl`, `kanban.json`, session files, or projections.
- Never start by arbitrary `pattern_id`; public proposals use catalog
  `route_id`.
- A proposal is not approval. Do not run `--apply` unless the operator has
  supplied an explicit approved proposal and the current capability allows it.
- Do not create a new topology from chat text. Use the workflow-config proposal
  path when no active route can satisfy the task.

## Method

1. Confirm that a real Task exists. If the user has not requested tracking,
   discuss the work normally. If they requested execution but no Task exists,
   propose `create-task` first.
2. Read the active choices:

   ```bash
   zf workflow routes --task TASK-ID --format json
   ```

3. Classify the goal semantically:
   - `delivery`: issue, PRD, feature, refactor, or implementation work;
   - `research`: evidence gathering before a product/delivery decision;
   - `review`: architecture, security, code, plan, or risk review;
   - `planning`: artifact or task-map production without implementation;
   - `general`: another explicitly registered route.
4. Remove routes that are unavailable or cannot satisfy required output.
5. Rank the remaining routes using the Task contract:
   - prefer a single lane for one bounded owner, narrow scope, and one direct
     verification path;
   - prefer multiple lanes when independent implementation surfaces, distinct
     expertise, or independent verification justify coordination cost;
   - use only roles and lane counts reported by the catalog;
   - prefer Research when a consequential decision lacks evidence;
   - prefer Delivery when acceptance and verification are already concrete.
6. If one missing answer materially changes the route or parameters, ask one
   Plan question with two or three mutually exclusive options. Put the
   recommended option first and allow a custom answer. Do not ask for secrets.
7. Once inputs are sufficient, create a durable proposal:

   ```bash
   zf workflow start \
     --task TASK-ID \
     --route ROUTE-ID \
     --objective "TASK-SPECIFIC OBJECTIVE" \
     --parameters-json '{"expected_output":"..."}' \
     --propose \
     --format json
   ```

8. Report the `proposal_event_id`, selected route, topology, roles, and unresolved
   risks. Do not claim that the Workflow started.

## Apply

Only an explicitly authorized operator process outside the provider session may
apply. The provider must not inspect, request, or receive
`ZF_WORKFLOW_ACTION_TOKEN`; return the proposal event id to the operator instead.

```bash
zf workflow start \
  --proposal-event-id EVENT-ID \
  --authorization-ref APPROVAL-REF \
  --authorization-token "$ZF_WORKFLOW_ACTION_TOKEN" \
  --apply \
  --format json
```

The operator host must configure `ZF_WORKFLOW_ACTION_TOKEN` without exporting it
into a Coding Agent or other provider process. Before apply, ensure the approval
refers to the exact proposal. Let the CLI reject missing/invalid authorization
and stale Task/config bindings. Never repair a rejection by editing runtime
truth or substituting a different route silently.

## Output Contract

For recommendation or clarification, include:

- Task id and concise goal classification;
- recommended `route_id` and why it fits;
- topology, roles, lane count, and expected output from the catalog;
- one alternative and its tradeoff when useful;
- missing parameter or blocker;
- proposal event id after `--propose`.

How to test: create a temporary Task, run `zf workflow routes`, propose
`research:fixed` or an active Delivery route, verify no
`workflow.invoke.requested` exists before apply, then apply the exact proposal.

## 未决 auto 与结果复用

`flow intake/classify --kind auto` 的关键词输出仅为候选提示，不能作为路线决定。
先按目标、约束与验收进行语义判断；否定句、引用示例和混合意图不能按关键词启动。
已有有效路由建议时复用其 Request/Requirement revision 和目录绑定；否则澄清或通过
既有 Workflow Synthesis Operation 给出建议。Provider 失败保持未决，不能默认为 issue。
显式有效路线不额外调用分类模型；旧请求按原版本恢复。
