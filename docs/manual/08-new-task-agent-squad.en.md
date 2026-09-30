# Create Tasks, Assignment Intent, and Agent Collaboration

ZaoFu does not use a standalone **New Task** form to create and immediately
dispatch a Worker. A tracked Task starts as an exact **Create Task** proposal
from Kanban Agent or from a confirmed Channel PRD. An operator approves that
proposal before a controlled action writes it into the selected Project.
Creation, assignment intent, and runtime dispatch are separate steps.

## Create a Tracked Task

1. Select the target Project in the Web sidebar.
2. Open Kanban Agent and state the objective, scope, acceptance criteria, and
   priority. Alternatively, converge a requirement in a Channel Group and use
   **Create Task from PRD**.
3. Continue the conversation when inputs are incomplete; do not approve an
   ambiguous proposal.
4. Review the exact title, objective, acceptance criteria, and priority in the
   **Create Task** proposal, then approve it.
5. Return to the **Tasks** Board and open the resulting Task by ID.

Approval invokes the controlled `create-task` action for the current Project's
TaskStore and event ledger. Chat text, a Channel conclusion, or a UI selection
does not directly launch an Agent.

### Attach Files in Kanban Agent

Use the `+` at the lower-left of the composer to select local images, logs,
Markdown, JSON, Office/PDF, and other supported files. You can also paste a
screenshot directly into the composer. Removable attachment cards appear before
sending. Each file is limited to 20 MB; one message accepts up to five files and
100 MB in total.

For an ordinary conversation, ZaoFu stores the bytes as verified sidecars under
the selected Project and records only refs and digests in events. Codex receives
images as native image input; other providers can inspect the prompted read-only
local paths. With `/issue <description>`, the same files become attachments on
the Self-Issue Draft and remain private until the user chooses to publish them.

## Read Complete Context from a Task

Task detail separates long-running delivery state into four views:

- **Summary** shows the current contract, dependencies, assignee, handoff, and
  assignment intent;
- **Activity** shows the event timeline, current route, execution DAG, and wait reason;
- **Evidence** shows the artifact ledger, final Task Map, Git refs, and test evidence;
- **Advanced** shows attempts, sessions, provider, context, skills, and deeper runtime data.

The **Agents** page adds Worker health, context and token usage, provider, cost,
and current Task. Correlate both views by Task, Run, and attempt identity rather
than inferring execution state from an Agent name.

![Animated Task context, activity, evidence, and Agent resources](assets/task-context-handoff.webp)

## Propose Assignment Intent

Open **Assignment Intent** in the Task's **Summary** view and provide only the
fields that should change:

- **Role** for the intended role or instance;
- **Backend** for a configured Codex, Claude Code, or other backend;
- **Channel** for a related Channel Group;
- **Supervisor** for the intended observation surface;
- **Reason** for the proposal.

Selecting **Propose Assignment** appends `assignment.intent.proposed`:

```json
{
  "type": "assignment.intent.proposed",
  "payload": {
    "task_id": "TASK-...",
    "role": "dev-ui",
    "backend": "codex",
    "channel_id": "",
    "supervisor": "",
    "reason": "operator assignment intent",
    "dispatches": false
  }
}
```

`dispatches=false` is an invariant. This event records auditable intent; it
does not replace the current Worker and is not `task.dispatched`. Work starts
only through an approved Task-bound Workflow or a kernel-controlled dispatch
action.

## Agent, Kanban Agent, and Channel Group

- An **Agent** performs implementation, testing, review, or research and reports
  artifacts, evidence, or controlled-action requests.
- **Kanban Agent** is the Project's general Coding Agent and operator surface. It
  clarifies requests and prepares proposals without bypassing approval or the
  kernel state machine.
- A **Channel Group** lets people and multiple Agent roles turn an ambiguous
  question into a confirmed PRD. Its transcript is collaboration context, not
  Task truth.

The recommended flow is: discuss and clarify -> exact Task proposal -> operator
approval -> Task contract -> assignment/workflow proposal -> controlled
approval -> kernel dispatch -> evidence writeback.

## Verification Checklist

- A Task or assignment intent created in Project A must not appear in Project B's state directory.
- An unapproved **Create Task** proposal must not create a canonical Task.
- `assignment.intent.proposed` must pass schema validation, retain the original
  request, and keep `dispatches=false`.
- The assignment proposal must not directly produce `task.dispatched`.
- Summary, Activity, Evidence, and Advanced must resolve current facts for the
  same Task and Run.

Use a temporary Project and state directory for real-provider smoke tests. If
no provider is available, at minimum verify the Web/API action, event schema,
Project isolation, and no-direct-dispatch invariant.
