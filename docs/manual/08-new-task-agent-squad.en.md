# New Task, Agent, and Squad

ZaoFu Web's **New Task** is a project-scoped work-request entry point. It lets
an operator choose a project, provide an issue/task contract, and select an
assignee. The kernel boundary remains unchanged: creating a task writes only to
that project's `TaskStore`; selecting an agent or squad records an
`assignment.intent.proposed` event and does not directly launch a headless
worker.

## Create an Issue or Task

1. Select the target project in the Web sidebar.
2. Open **New Task** from the board and provide a title, behavior, and verification.
3. Select a priority and assignee:
   - **Unassigned** puts the task on Kanban for later manual or automated triage.
   - **Agent** selects a headless agent instance projected in the current project.
   - **Squad** selects a channel-backed group; later routing or its owner decides how to split work.
4. Select **Create Task**.

Web first invokes `create-task`. If an agent or squad was selected, it then
invokes `assignment-propose`, appending an event to the same project's
`events.jsonl`:

```json
{
  "type": "assignment.intent.proposed",
  "payload": {
    "task_id": "TASK-...",
    "assignee_type": "agent|squad",
    "assignee_id": "...",
    "assignee_label": "...",
    "dispatches": false
  }
}
```

`dispatches=false` is an invariant. The event records intent and audit context;
it is not a runtime dispatch.

## Agent, Kanban Agent, and Squad

- An **Agent** is a headless worker instance that assignment intent can target,
  such as Claude Code or Codex. It is not another control plane.
- The **Kanban Agent** is a controlled Web/API operator surface. It converts
  intent into deterministic actions such as `create-task`, `update-task`, and
  `assignment-propose`.
- A **Squad** currently maps to a channel-backed group. After New Task records
  the squad intent, its owner, routing logic, or an operator decides whether to
  create subtasks, select agents, and start headless workers.

The flow is: Kanban Agent receives an operation, the kernel appends events and
updates task truth, and an Agent or Squad later consumes those facts through a
controlled runtime path. The Web selector must not directly write worker
sessions, workdirs, progress, or memory.

## ZaoFu Boundary Decisions

- Keep the three-part intake: project selection, issue/task contract, assignee.
- Represent assignees with `assignee_type`, `assignee_id`, and
  `assignee_label`, distinguishing unassigned, agent, and squad.
- Convert a direct agent queue operation into `assignment.intent.proposed`;
  only a kernel-controlled path may decide to dispatch.
- Map squad assignment to channel-backed group intent. A leader or Supervisor
  is a coordination surface, not a new scheduler.
- Do not add a workspace-global issue table, launch workers from the picker, or
  treat channel/squad transcripts as task truth.

## Verification Checklist

- An assignment created in Project A must not appear in Project B's state directory.
- A squad assignee must not write a channel ID into `TaskStore.assigned_to`.
- An agent assignee may set `TaskStore.assigned_to`, but must not emit `task.dispatched`.
- `assignment.intent.proposed` must pass schema validation and retain the original request for audit.

For real-provider smoke tests, use a temporary project and state directory. If
Claude Code or Codex headless is unavailable, at minimum verify the Web/API
action, event schema, project isolation, and no-direct-dispatch invariant.
