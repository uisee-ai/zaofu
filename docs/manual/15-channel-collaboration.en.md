# Channel Collaboration Guide

[中文](15-channel-collaboration.md) · [Shortest Channel-to-PRD path](workflows/channel-to-prd.en.md)

> For operators who want people and multiple agents to discuss requirements,
> produce an Owner-confirmed PRD, and continue through Web, Kanban Agent, or
> Feishu.
>
> Current as of 2026-08-03 against code and tests.
> The default Channel mode is `conversation`; creation does not automatically
> fan out, and `multi_lens` must be started explicitly.

## 1. Preflight

Start real-provider Channels through the trusted-local canonical launcher:

```bash
tools/start-webkanban.sh --host 127.0.0.1 --port 8001
tools/start-webkanban.sh --port 8001 --status
```

Shared or untrusted hosts must use the normal sandbox. A trusted-local bypass
does not give every Channel Member Project write permission. See
[Real-provider preflight](16-real-codex-provider-preflight.en.md).

## 2. What A Channel Is

```text
Channel
├── Origin / Owner / Leader
├── Members and provider bindings
├── Messages, threads, ACK/NACK
├── Discussion mode and explicit actions
├── PRD draft / revision / owner confirmation
└── Result and workflow receipts
```

A Channel is a dynamic runtime object maintained through `channel.*` events,
sanctioned sidecars, and Channel contracts. It is not a static group block in
`zf.yaml`.

Channel and Workflow are separate state machines:

- Channel creation does not require a Task;
- Channel owns clarification, review, decisions, and PRD finalization;
- Channel does not schedule Tasks, modify code, or decide delivery terminal state;
- a confirmed PRD does not automatically create a Task;
- only a controlled Task/Workflow proposal approved by a human enters Kernel Workflow;
- Delivery receipts return read-only and do not let Channel own runtime state.

## 3. Three Product Modes

| Mode | Default trigger | Use case | Engine mapping |
|---|---|---|---|
| `conversation` | Default after creation | Natural chat, targeted mentions, continued discussion | `manual_mention` |
| `clarification` | Explicit selection or template | Resolve open questions and missing decisions | `mention_relay` |
| `multi_lens` | Explicit Discuss | Independent views, relay/critique, and synthesis | `fanout_then_synthesis` |

Engine mappings are compatibility details, not additional product modes.
`max_rounds` bounds explicit discussion; it does not make `conversation` wake
every member automatically.

## 4. Built-In Templates

The code contains five versioned templates at version `2026-07-31.1`:

| Template | Intended use | Required members | Default mode | Leader / default responder | Default budget ceiling |
|---|---|---|---|---|---|
| `prd-clarification` | Converge PRD scope, scenarios, and acceptance | `product_pm`, `arch`, `critic`, `synthesizer`; optional `security_reviewer` | `conversation` | `product_pm` / `synthesizer` | 20 rounds, parallel 5 |
| `research-review` | Verify sources, grade evidence, and compare approaches | `researcher`, `arch`, `critic`, `synthesizer` | `conversation` | `researcher` / `synthesizer` | 16 rounds, parallel 4 |
| `architecture-review` | Review architecture, implementation parity, security, and candidate gates | `arch`, `security_reviewer`, `dev_reviewer`, `critic` | `multi_lens` | `arch` / `arch` | 16 rounds, parallel 4 |
| `quick-change` | Handle a frozen small feature or defect | `tech_leader`, `dev_reviewer`, `qa_analyst` | `conversation` | `tech_leader` / `tech_leader` | 12 rounds, parallel 3 |
| `incident-triage` | Gather incident evidence, assess impact, and recommend recovery | `tech_leader`, `qa_analyst`; optional `security_reviewer` | `clarification` | `tech_leader` / `tech_leader` | 12 rounds, parallel 3 |

Budgets are ceilings, not required conversation lengths. A Kanban Agent Plan
may tighten a request through `budget.max_rounds`, `max_parallel_replies`, and
phase deadlines.

Templates fix required roles, skill refs, allowed overrides, and writer scope;
an override cannot add arbitrary roles. Every skill ref must resolve before
creation. Materialization starts every Member with a `read_only` permission
profile and ceiling. The Leader additionally receives `propose_workflow`.
`writer_role` and `writer_scope` describe artifact responsibility and a
controlled write boundary; they do not grant filesystem write permission.

Template boundaries are:

- `prd-clarification` owns the question ledger, Owner clarification, and the
  PRD/requirement snapshot without bypassing Create Task or Workflow approval;
- `research-review` discusses evidence without implicitly starting Research;
- `architecture-review` produces multi-lens findings and Workflow suggestions,
  not implementation;
- `quick-change` is only for a frozen, bounded change;
- `incident-triage` diagnoses and proposes controlled actions while Kernel or
  an approved action performs recovery.

Messages without an explicit mention route to the default responder. An
explicit `@role` always takes precedence.

## 5. Create Through Kanban Agent

```text
Create a PRD discussion Channel for the login-security change.
Invite product, architecture, and security perspectives. Start with natural
conversation and do not create a Task or start a Workflow automatically.
```

Review the action-bound setup Plan for:

- `template_id`, name, origin, and Owner;
- required and optional members;
- provider/model overrides;
- product mode, budget, and optional roles;
- Leader and `propose_workflow` authority;
- source receipt target.

Every option in an action-bound Channel setup Plan must explicitly bind one of
`conversation|clarification|multi_lens`; it cannot silently inherit the template
default. The confirmation surface also shows whether initial routing is a
single responder, facilitated relay, or N-way blind fanout. An old pending Plan
without a mode fails closed at apply time, while completed historical receipts
remain idempotently readable.

`Create & start` atomically performs:

```text
channel-create-and-start
  -> create Channel
  -> materialize Members, skills, permissions, and profile binding
  -> post the clean business requirement without control instructions, with durable ACK/NACK
  -> initialize the product mode
  -> conversation waits for directed interaction
```

The word `start` in the button does not imply automatic fanout. Bounded
multi-perspective work runs only when the mode is already `multi_lens` or an
explicit Discuss action follows.

`Chat about` returns additional context to the same Kanban Agent session and
revises the Plan. It does not execute an option or require hand-edited JSON.

## 6. Natural And Multi-Lens Discussion

In `conversation`:

- people, the Leader, or authorized Members post messages;
- mentions target only the needed Member;
- the same thread continues without rebuilding the Channel;
- reply deltas stream through SSE/sidecars while terminal bodies and refs persist;
- provider failure remains diagnosable and does not automatically rerun every Member.

When independent views are needed, run Discuss / `multi_lens` explicitly:

```text
phase1 blind answers
  -> phase2 relay / critique
  -> phase3 synthesis
```

This operation is bounded by rounds, members, and budget and links back to the
normal conversation history. It is an explicit operation, not the permanent
default state of a Channel.

After convergence the Channel remains interactive. People can ask follow-up
questions, add requirements, or explicitly reopen multi-lens discussion without
recreating the Channel. When the projection has an Owner-question frontier,
Web presents at most the current first three questions in sequence. Enumerable
questions use two or three mutually exclusive options and one recommendation;
open questions use free text. Submission still records individual resolved
facts in the `channel.question.*` ledger, so the component does not become a
second question state machine.

When an active discussion needs a mode change or a fresh review, use
`Restart discussion` in Details. The controlled action first closes the old
session with `cancelled / explicit_restart`, then starts the selected mode with
a fresh trigger-message identity. The original requirement is retained only as
`source_requirement_message_id`, so ingress idempotency cannot mistake the new
fanout for a duplicate post.

![Natural discussion, directed replies, and multi-role convergence in a Channel Group](assets/quickstart-channel-discussion.webp)

## 7. PRD Finalize And Owner Authority

```text
conversation / clarification / multi_lens
  -> explicit Finalize
  -> PRD draft artifact
  -> Continue | Revise | Owner confirm
  -> channel.consensus.reached(ref, digest, revision)
  -> exact-origin PRD receipt
```

Required invariants:

- the complete PRD body lives in a sanctioned artifact/sidecar;
- the readiness artifact must carry a `ready`, `needs_owner`, or `needs_multi_lens` verdict; an `unassessed` PRD cannot be Owner-confirmed;
- events carry identity, revision, digest, preview/ref, and causation;
- revision updates use currentness/CAS so an old revision cannot replace a newer confirmed result;
- only Owner confirm creates the canonical PRD;
- the receipt returns to the exact origin and remains retryable without duplicate confirmation;
- synthesis, an agent claim, or majority opinion does not replace Owner confirmation.

## 8. Handoff From PRD To Workflow

```text
confirmed PRD
  -> existing Task
     or Create Task proposal -> human confirm
  -> Task-bound Workflow Plan
  -> exact workflow-start proposal
  -> independent Approve
  -> Kernel Workflow
  -> Task/Run/Delivery receipt back to Channel
```

Only the exact `leader_member_id` with `propose_workflow` permission may create
the handoff proposal. It cannot approve the proposal, read the operator token,
or emit `workflow.invoke.requested` directly.

See [Controlled Workflow start](workflows/controlled-workflow-start.en.md).

## 9. Channel Versus Research Workflow

`research-review` is a discussion template for existing material and defaults
to conversation. A real Research Workflow requires:

1. a real Task;
2. an explicit Research request;
3. an active Research route returned by `zf workflow routes --task TASK-ID`;
4. route selection in Plan;
5. independent approval of the exact proposal.

Research output is an auditable artifact and does not automatically become a
PRD Workflow.

## 10. CLI Message Entry

```bash
zf channel say CHANNEL-ID \
  --text "Add failure cases and ask @critic to verify them." \
  --member-id reviewer \
  --mention critic
```

This uses the `channel-post-message` ControlledAction and does not edit
`events.jsonl`. Finalize, Owner confirmation, member authority, and Workflow
handoff currently use Web, Kanban Agent, Feishu, or another token-gated action.

## 11. Feishu Binding

A Feishu chat can route into an existing Channel. Inbound messages retain their
origin, and PRD/result receipts return to the same source. The Bridge publishes
messages, intent, refs, or controlled-action requests; it does not modify Task,
Workflow, or Run state directly. See the
[Feishu AI-Native Bridge](19-feishu-ai-native-direct-bridge.en.md).

## 12. Observation And Diagnosis

```bash
zf events --last 100
zf status --workers
```

Check:

- template digest, mode, Owner, Leader, and roster;
- exactly-once origin admission with ACK/NACK;
- no unexpected fanout in conversation mode;
- member, round, budget, and terminal refs for a Discuss operation;
- PRD draft/confirmed revision, digest, and exact-origin receipt;
- exact-Leader handoff followed by independent Approve;
- idempotent provider failure, retry, and result receipt;
- fail-closed behavior for a missing required sidecar.

## Definition Of Done

A Channel is complete when it remains interactive, the Owner-confirmed PRD is
traceable, and the source receipt succeeds. Software delivery completion belongs
to Task, Workflow, Run, and Delivery, not Channel synthesis.
