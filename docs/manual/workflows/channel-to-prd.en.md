# Channel To PRD

[中文](channel-to-prd.md) · [Workflow index](README.en.md)

> Current contract: new Channels default to `libre_discuss`. Creation is quiet, with no
> discussion seed or automatic first round. Users start the conversation and choose when to
> generate a PRD. Existing Channels and the three legacy modes retain their behavior.
> Generating or confirming a PRD never creates a Task or starts a Workflow automatically.

## When To Use It

Use a Channel when:

- a request has several plausible interpretations and needs owner, product, architecture, security, or critical perspectives;
- the result must return exactly to its Web, Feishu, or Kanban Agent origin;
- the team wants a canonical PRD before deciding whether to start delivery.

Do not use it when:

- a clear, small problem fits one agent session;
- a Task already exists and only needs a Workflow selection;
- chat members are expected to modify the Project or bypass approval.

## Product Modes

| Mode | Use it when... | Behavior |
|---|---|---|
| `libre_discuss` | Default for new, user-led discussions | The host replies without a mention; targeted/@all chat; manual PRD generation |
| `conversation` | Explicit legacy natural discussion and targeted mentions | The user or Leader chooses who responds; no automatic fanout |
| `clarification` | Fields, boundaries, or owner decisions are missing | Resolve open questions one by one |
| `multi_lens` | Several independent views and synthesis are explicitly needed | An explicit Discuss action runs bounded fanout and synthesis |

Compatibility engine names such as `manual_mention`, `mention_relay`, and
`fanout_then_synthesis` map into these modes. They are not additional top-level
product choices.

## Create A Channel

Ask Kanban Agent:

```text
Create a PRD discussion Channel for the login-security change.
Create a quiet free-discussion group with product, architecture, and security
perspectives. I will start chatting and generate the PRD manually.
```

Review the setup Plan for:

- template, Channel name, and Owner;
- required and optional members;
- provider/model overrides;
- discussion mode and budget;
- Leader, permissions, and allowed handoff;
- source/origin receipt target.

Creation materializes the Channel, members, skills, and permissions. Free discussion
does not copy setup instructions or silently inject the project brief into a first round.
Users may explicitly select the brief and other materials when generating a PRD.

## Free Discussion And Manual Generation

- With no mention the host responds. A single mention calls only that member; `@all` includes the current eligible members.
- After `@all` the host may arrange one necessary targeted follow-up, without an ongoing broadcast or consensus loop.
- Profile creation, manual invitations and host invitation proposals remain. Approval admits a member quietly; mention them afterward or include them in the next `@all`.
- A round with at least two distinct replies and two valid questions is deduplicated before answer cards appear. Preserve distinct decisions, scenarios, options and dissent. On failure, raw cards are labelled and remain answerable.
- Answering cards does not generate a PRD. Unanswered cards do not lock the generation entry.
- Open “Generate / View PRD”, choose materials or the project brief, and request a draft explicitly.
- The accepted request freezes its inputs. Later replies and edits belong to the next version; chat remains usable during generation.
- Cancel only this artifact request, retaining chat and previous PRDs. Retry reuses the snapshot; a new version uses the latest selected inputs.

```text
quiet creation -> user-led chat, questions and additions
                              -> explicit generation -> draft -> evaluation, revision and confirmation
```

Legacy modes retain their existing progression. Historical demonstrations show that legacy flow, not automatic free-discussion behavior.

## Move From PRD To Delivery

The confirmed PRD still needs a separate handoff:

```text
confirmed PRD
  -> existing Task
     or Create Task from PRD proposal -> human confirm
  -> Task-bound Workflow Plan
  -> exact Workflow proposal
  -> Approve
  -> Kernel starts Workflow
  -> read-only Task/Run/Delivery receipt back to the original Channel
```

Only the exact `leader_member_id` with `propose_workflow` permission may create
the handoff proposal. The Leader still cannot approve its own proposal and must
not receive `ZF_WORKFLOW_ACTION_TOKEN`.

## Add A Message From CLI

The stable low-level message command is:

```bash
zf channel say CHANNEL-ID \
  --text "Add failure cases and ask @critic to verify them." \
  --member-id reviewer \
  --mention critic
```

Finalize, Owner confirm, member authority, and Workflow handoff currently use
Web/Kanban Agent, Feishu, or controlled-action surfaces. Do not simulate them by
editing `events.jsonl`.

## Observe And Diagnose

Check that:

- the origin message was admitted exactly once with an explicit ACK/NACK;
- the current product mode is expected;
- multi-lens work began only after an explicit action;
- PRD ref, digest, revision, and owner identity are complete;
- the receipt returned to the exact origin;
- Task/Workflow proposals bind the confirmed PRD revision;
- non-Leaders or unauthorized members cannot hand off;
- provider failures are deduplicated and remain recoverable.

```bash
zf events --last 100
zf status --workers
```

## Definition Of Done

Channel-to-PRD is complete when the Owner has confirmed a PRD with a ref,
digest, and revision and the origin has received its receipt. Delivery starts
only after a real Task exists, a Workflow is approved, and the Kernel executes
it.

## Related

- [Detailed Channel guide](../15-channel-collaboration.en.md)
- [Controlled Workflow start](controlled-workflow-start.en.md)
- [Feishu AI-Native Bridge](../19-feishu-ai-native-direct-bridge.en.md)
