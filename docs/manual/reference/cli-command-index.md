# ZaoFu CLI 命令目录

> 本文件由 `src/zf/cli/main.py::build_parser()` 生成，禁止手工修改。
> 重新生成：`uv run python scripts/manual-docs.py generate`。

当前共 **69** 个顶层命令 family、**299** 条可寻址命令路径。
命令描述直接取自 argparse parser，因此描述语言以代码中的 help 为准。

## `zf agents`

List agent CLIs / unblock a parked worker lane

| Command | Parser description |
|---|---|
| `zf agents` | List agent CLIs / unblock a parked worker lane |
| `zf agents unblock` | Clear blocked_human on a worker lane (operator redrive) |

## `zf archive-run`

Archive a run live state into .zf/runs/<run_id>

| Command | Parser description |
|---|---|
| `zf archive-run` | Archive a run live state into .zf/runs/<run_id> |

## `zf artifact`

Artifact manifest helpers

| Command | Parser description |
|---|---|
| `zf artifact` | Artifact manifest helpers |
| `zf artifact catalog` | Query rebuildable artifact metadata and lineage |
| `zf artifact catalog lineage` | Show subject-to-artifact lineage |
| `zf artifact catalog list` | List artifact objects |
| `zf artifact catalog show` | Show one artifact identity |
| `zf artifact list` | List immutable inputs available to one provider attempt |
| `zf artifact manifest` | Artifact manifest commands |
| `zf artifact manifest create` | Create a deterministic artifact manifest JSON |
| `zf artifact read` | Read one attempt input and append read evidence |

## `zf attach`

Attach to a running role

| Command | Parser description |
|---|---|
| `zf attach` | Attach to a running role |

## `zf attempt`

Attempt-level artifact queries

| Command | Parser description |
|---|---|
| `zf attempt` | Attempt-level artifact queries |
| `zf attempt inspect` | Inspect attempt inputs and handoff state |
| `zf attempt missing-reads` | List missing required reads |

## `zf autopilot`

Run deterministic Autopilot proposal checks

| Command | Parser description |
|---|---|
| `zf autopilot` | Run deterministic Autopilot proposal checks |
| `zf autopilot tick` | Scan runtime state and create proposals |

## `zf autoresearch`

Run outer autoresearch harness evaluation supervisor

| Command | Parser description |
|---|---|
| `zf autoresearch` | Run outer autoresearch harness evaluation supervisor |
| `zf autoresearch campaign` | Plan multi-scenario autoresearch campaigns |
| `zf autoresearch campaign plan` | Write a runnable campaign plan without starting providers |
| `zf autoresearch compare` | Compare baseline/candidate eval-result.v1 artifacts |
| `zf autoresearch discover-bugs` | Detect autoresearch failure signals and export source backlogs |
| `zf autoresearch export-eval-result` | Export command or run_dir evidence as eval-result.v1 |
| `zf autoresearch loop` | Closed-loop autoresearch + eval + LLM reflection. Runs scenarios in rotation, evaluates delta vs prior iter, reflects on whether a better fix exists, writes journal.jsonl + iter-NNN.md, and waits for parent HEAD to change between iter (so inner harness can land fixes). |
| `zf autoresearch migrate-worktrees` | Audit legacy resident worktrees; defaults to dry-run |
| `zf autoresearch resident` | Opt-in resident consumer for autoresearch loop requests |
| `zf autoresearch review-gate` | Prepare and close out opt-in autoresearch review fanout artifacts |
| `zf autoresearch review-gate closeout` | Validate autoresearch.review_council.v1 synth artifact |
| `zf autoresearch review-gate prepare` | Generate codebase context and failure evidence packs |
| `zf autoresearch run` | Run an autoresearch evaluation |
| `zf autoresearch self-repair` | Supervised self-repair maintenance helpers |
| `zf autoresearch self-repair checkpoint` | Create task checkpoint |
| `zf autoresearch self-repair prepare` | Enter maintenance mode |
| `zf autoresearch self-repair validate` | Mark repair validation result |
| `zf autoresearch triggers` | Evaluate autoresearch trigger policy |
| `zf autoresearch triggers scan` | Read-only trigger scan |

## `zf backlog`

Backlog/task document maintenance helpers

| Command | Parser description |
|---|---|
| `zf backlog` | Backlog/task document maintenance helpers |
| `zf backlog audit` | Report stale active backlog/task docs |
| `zf backlog goal` | Project a feature goal and its mapped work units |
| `zf backlog integration` | Project a feature integration item |
| `zf backlog resume-packet` | Build a runtime-generated resume packet for a task |
| `zf backlog retry-metadata` | Project retry/continuation metadata for a task |
| `zf backlog why-not-done` | Explain why a task is not terminally done |
| `zf backlog workpad` | Project task workpad/progress facts from runtime state |

## `zf bridge`

Run external bridge integrations

| Command | Parser description |
|---|---|
| `zf bridge` | Run external bridge integrations |
| `zf bridge openclaw-feishu` | [DEPRECATED] Relay ZaoFu to Feishu via OpenClaw; use direct \`zf feishu push\` / \`zf feishu bridge --watch\` instead |
| `zf bridge openclaw-feishu inbound` | Apply inbound Feishu/OpenClaw payload |
| `zf bridge openclaw-feishu push` | Push outbound ZaoFu channel messages |
| `zf bridge openclaw-feishu status` | Show bridge delivery counters |

## `zf bug-fix-cycle`

Drive the ZaoFu operator fix cycle after zaofu.bug.detected

| Command | Parser description |
|---|---|
| `zf bug-fix-cycle` | Drive the ZaoFu operator fix cycle after zaofu.bug.detected |

## `zf channel`

Channel operations

| Command | Parser description |
|---|---|
| `zf channel` | Channel operations |
| `zf channel say` | Post a message to a channel (gated; projected to Feishu) |

## `zf chat`

Append an operator message intent

| Command | Parser description |
|---|---|
| `zf chat` | Append an operator message intent |

## `zf check`

Project health checks

| Command | Parser description |
|---|---|
| `zf check` | Project health checks |
| `zf check artifact-matrix` | Evaluate a generic artifact/matrix gate config |
| `zf check clean-state` | Check project cleanliness |
| `zf check doc-sync` | Check documentation sync |
| `zf check preflight` | Run a lightweight coding check lane; diff and checks produce evidence without a verdict |
| `zf check task-docs` | Audit task capsule drift |

## `zf cleanup`

Run periodic cleanup

| Command | Parser description |
|---|---|
| `zf cleanup` | Run periodic cleanup |

## `zf config`

Inspect/render the effective canonical config

| Command | Parser description |
|---|---|
| `zf config` | Inspect/render the effective canonical config |
| `zf config inspect` | Inspect expanded config |
| `zf config render` | Render expanded config and lock |

## `zf cost`

Show cost breakdown

| Command | Parser description |
|---|---|
| `zf cost` | Show cost breakdown |

## `zf ctx`

agent-facing context pull(task capsule/manifest/events)

| Command | Parser description |
|---|---|
| `zf ctx` | agent-facing context pull(task capsule/manifest/events) |

## `zf doctor`

Run operator diagnostics

| Command | Parser description |
|---|---|
| `zf doctor` | Run operator diagnostics |
| `zf doctor contract-authority` | Check canonical Task contract CAS lineage and receipts |
| `zf doctor event-contract` | Check workflow event producer/consumer contracts |
| `zf doctor panes` | Check pane-grid role bindings |
| `zf doctor provider` | Check provider CLI preflight |
| `zf doctor sidecar` | Check sidecar refs referenced by events |
| `zf doctor task-attempt` | Report TaskAttempt shadow-to-enforce readiness |
| `zf doctor workdirs` | Check runtime workdirs |

## `zf emit`

Emit an event

| Command | Parser description |
|---|---|
| `zf emit` | Emit an event |

## `zf eval`

Static evaluation of zaofu config

| Command | Parser description |
|---|---|
| `zf eval` | Static evaluation of zaofu config |
| `zf eval output` | Independently evaluate a frozen text output |
| `zf eval preset` | Statically evaluate a zf.yaml preset |

## `zf events`

Query events

| Command | Parser description |
|---|---|
| `zf events` | Query events |
| `zf events trace` | Show the causation chain of an event |

## `zf evolution`

Manage evidence-bound self-evolution attempts and capabilities

| Command | Parser description |
|---|---|
| `zf evolution` | Manage evidence-bound self-evolution attempts and capabilities |
| `zf evolution asset-export` | Export a retained asset |
| `zf evolution asset-import` | Import a portable asset as an inactive target-validation candidate |
| `zf evolution asset-outcome` | Record one idempotent learning-asset usage outcome |
| `zf evolution asset-propose` | Propose a learning asset |
| `zf evolution asset-target-validate` | Record controlled target-project validation for an imported asset |
| `zf evolution asset-transition` | Record an externally controlled asset lifecycle receipt |
| `zf evolution attempt` | Materialize an evolution attempt |
| `zf evolution challenge-decide` | Promote or reject a stable shadow challenge with evaluator receipt |
| `zf evolution challenge-materialize` | Materialize a visible shadow challenge candidate |
| `zf evolution compare` | Compare settled repeated A/B trials |
| `zf evolution economics` | Compute evidence-bound evolution economics without inventing values |
| `zf evolution evaluator-register` | Register public evaluator metadata and sealed cases |
| `zf evolution opportunity-propose` | Materialize a proposal-only evolution opportunity |
| `zf evolution skill-maintenance-propose` | Record an optimize/replace/merge/deactivate Skill proposal |
| `zf evolution skill-opt-agent-execute` | Execute one proposal-only Optimizer Agent request |
| `zf evolution skill-opt-export` | Export the completed optimizer best as a design-179 Skill candidate |
| `zf evolution skill-opt-init` | Initialize one bounded, frozen single-Skill optimization campaign |
| `zf evolution skill-opt-prepare` | Apply one bounded Agent-proposed Skill edit packet |
| `zf evolution skill-opt-selection-submit` | Validate and publish one sealed optimizer Selection result |
| `zf evolution skill-opt-settle` | Select one prepared Skill candidate against held-out evidence |
| `zf evolution skill-outcome` | Credit a skill only when current-dispatch invocation is observed |
| `zf evolution skill-overlay-resolve` | Resolve scoped Skill canary overlays for a future dispatch |
| `zf evolution skill-routing-report` | Build a routing-stress report from typed observation sidecars |
| `zf evolution skill-source-apply` | Apply an exact Skill retain proposal and sync provider copies |
| `zf evolution skill-source-propose` | Build an owner-approved source-retain proposal from a passed canary |
| `zf evolution skill-treatment-compare` | Check that Skill treatment arms differ only by the target Skill |
| `zf evolution skill-trial-materialize` | Materialize one frozen Skill trial arm through the normal resolver |
| `zf evolution skill-trial-spec` | Build a frozen raw/current/candidate Skill trial specification |
| `zf evolution status` | Show the read-only evolution projection |
| `zf evolution trial-ensure` | Ensure a stable A/B trial row |
| `zf evolution trial-execute` | Execute one resident-owned evolution trial/canary request |
| `zf evolution trial-settle` | Settle one trial attempt |
| `zf evolution trial-start` | Claim a trial lease |
| `zf evolution variant-compare` | Materialize a Pareto comparison for workflow/provider variants |
| `zf evolution variant-current` | Check provider comparison fingerprints against current routes |
| `zf evolution workflow-learning-propose` | Compile Loop Learning into a standard Workflow Proposal |

## `zf failure`

Failure-to-eval utilities

| Command | Parser description |
|---|---|
| `zf failure` | Failure-to-eval utilities |
| `zf failure closeout` | Batch materialize failure candidates into backlog/eval/skill drafts |
| `zf failure materialize` | Materialize a failure-candidate.v1 into backlog/eval/skill draft |
| `zf failure promote` | Promote closeout backlog drafts into tasks/active after owner approval |
| `zf failure status` | List open failure candidates lacking any four-way closeout |

## `zf feature`

Manage features (high-level user goals)

| Command | Parser description |
|---|---|
| `zf feature` | Manage features (high-level user goals) |
| `zf feature add` | Add a new feature |
| `zf feature list` | List features |
| `zf feature show` | Show a feature in detail |
| `zf feature update` | Update a feature |

## `zf feishu`

Handle Feishu bridge commands

| Command | Parser description |
|---|---|
| `zf feishu` | Handle Feishu bridge commands |
| `zf feishu bridge` | One-shot turnkey reply: inbound message to channel to real agent reply |
| `zf feishu consume` | Ingest live Feishu events via lark-cli (WS long-conn, no webhook) |
| `zf feishu cron-template` | Print crontab entries for daily Automation and hourly Kanban sync |
| `zf feishu group` | Inspect or explicitly provision a project Feishu collaboration group |
| `zf feishu group attach` | Verify and attach an existing Feishu chat to the configured binding |
| `zf feishu group provision` | Create/verify the configured Feishu project group (external write) |
| `zf feishu group status` | Show project group binding state |
| `zf feishu handle` | Handle one Feishu webhook/message fixture |
| `zf feishu init-targets` | Create Feishu Docx/Base/Table targets for Automation and Kanban sync |
| `zf feishu live-smoke` | Run a cleanup-safe smoke against the real Feishu API |
| `zf feishu operate` | Run the headless Feishu operator agent over pending /zf ask messages |
| `zf feishu project-kanban` | Project task.status_changed events into Feishu Base |
| `zf feishu push` | Push event projections to Feishu channels |
| `zf feishu send-test` | Validate local Feishu bridge configuration without real API calls |
| `zf feishu serve` | Run a small webhook server that wraps zf feishu handle |
| `zf feishu sync-automation-insights-table` | Mirror Automation summaries and insights to a Feishu Bitable table |
| `zf feishu sync-automations` | Publish Automation daily/weekly/project reports to a Feishu document |
| `zf feishu sync-kanban-table` | Mirror the current Kanban projection to a Feishu Bitable table |

## `zf flow`

Draft and preflight short IssueFlow/PrdFlow/RefactorFlow specs

| Command | Parser description |
|---|---|
| `zf flow` | Draft and preflight short IssueFlow/PrdFlow/RefactorFlow specs |
| `zf flow clarify` | Revise and optionally confirm a workflow requirement |
| `zf flow classify` | Classify a workflow intake artifact |
| `zf flow draft` | Draft a short controller flow YAML |
| `zf flow intake` | Create a workflow intake artifact |
| `zf flow preflight` | Check start readiness |
| `zf flow start` | Build a safe flow-start proposal; use --dry-run for now |
| `zf flow submit` | Build a workflow submit event preview; use --dry-run for now |

## `zf gate`

Verification gates

| Command | Parser description |
|---|---|
| `zf gate` | Verification gates |
| `zf gate list` | List configured gates |
| `zf gate run` | Run a gate (or 'all') |

## `zf goal`

Show or set the run goal

| Command | Parser description |
|---|---|
| `zf goal` | Show or set the run goal |
| `zf goal set` | Set run goal objective/status |
| `zf goal show` | Show run goal projection |

## `zf guard`

Read-only worker guard checks

| Command | Parser description |
|---|---|
| `zf guard` | Read-only worker guard checks |
| `zf guard ownership` | Verify the actor still owns the active task before emitting completion |

## `zf handoff`

Generate handoff summary

| Command | Parser description |
|---|---|
| `zf handoff` | Generate handoff summary |

## `zf hook-recv`

Bridge a Claude Code hook to zaofu events.jsonl. Reads hook JSON from stdin.

| Command | Parser description |
|---|---|
| `zf hook-recv` | Bridge a Claude Code hook to zaofu events.jsonl. Reads hook JSON from stdin. |

## `zf init`

Initialize .zf/ state directory

| Command | Parser description |
|---|---|
| `zf init` | Initialize .zf/ state directory |

## `zf issue`

Validate or ingest an issue/bug candidate

| Command | Parser description |
|---|---|
| `zf issue` | Validate or ingest an issue/bug candidate |
| `zf issue ingest` | Ingest a candidate as a Kanban TaskContract |
| `zf issue validate` | Validate a candidate without changing state |

## `zf kanban`

Task board management

| Command | Parser description |
|---|---|
| `zf kanban` | Task board management |
| `zf kanban add` | Add a task to backlog |
| `zf kanban assign` | Assign a task to a role |
| `zf kanban export` | Export kanban to a human-readable artifact (default: md) |
| `zf kanban handoff` | Atomically update a task contract and assign the next owner |
| `zf kanban health` | Aggregated health snapshot (workflow / roles / failures / coordinator / metrics) |
| `zf kanban move` | Move a task to a new status |
| `zf kanban open` | List non-terminal tasks |
| `zf kanban pending` | List backlog tasks |
| `zf kanban ready` | List ready tasks |
| `zf kanban show` | Show task details |

## `zf logs`

View harness logs

| Command | Parser description |
|---|---|
| `zf logs` | View harness logs |

## `zf memory`

Memory management

| Command | Parser description |
|---|---|
| `zf memory` | Memory management |
| `zf memory add` | Add a memory entry |
| `zf memory check` | Check for stale entries |
| `zf memory show` | Show memory entries |

## `zf metrics`

Long-horizon metrics snapshot

| Command | Parser description |
|---|---|
| `zf metrics` | Long-horizon metrics snapshot |
| `zf metrics decision-ratio` | Orchestrator decision distribution + healthy-band check |
| `zf metrics diagnose` | Run MetricsEvaluator on current snapshot (health band + trend + recommendations) |
| `zf metrics snapshot` | Print current MetricsSnapshot |
| `zf metrics stability` | Evaluate stability over an events log |

## `zf panes`

Manage pane-grid bindings

| Command | Parser description |
|---|---|
| `zf panes` | Manage pane-grid bindings |
| `zf panes doctor` | Check pane-grid role bindings |
| `zf panes repair` | Repair pane-grid role bindings |

## `zf plan`

Review, approve, or reject a plan

| Command | Parser description |
|---|---|
| `zf plan` | Review, approve, or reject a plan |
| `zf plan approve` | Approve a plan and unlock writer fanout |
| `zf plan inspect-execution` | Inspect plan execution facts without approval or dispatch |
| `zf plan reject` | Reject a plan and request synthesis replan |
| `zf plan review` | List plans awaiting review with digest and checklist |

## `zf preflight`

Static dispatch-readiness checks before a real launch

| Command | Parser description |
|---|---|
| `zf preflight` | Static dispatch-readiness checks before a real launch |

## `zf presets`

List and show workflow presets

| Command | Parser description |
|---|---|
| `zf presets` | List and show workflow presets |
| `zf presets show` | Show preset details |

## `zf profile`

Detect project stack + recommend zf.yaml

| Command | Parser description |
|---|---|
| `zf profile` | Detect project stack + recommend zf.yaml |
| `zf profile bootstrap` | Detect + recommend + (optionally) materialize zf.yaml |
| `zf profile detect` | Detect the project stack |
| `zf profile recommend` | Recommend a zf.yaml archetype |

## `zf project`

Project-scoped review and insight commands

| Command | Parser description |
|---|---|
| `zf project` | Project-scoped review and insight commands |
| `zf project init` | Initialize a project container for issue/prd/refactor workflow intake |
| `zf project review-spine` | Review project design/delivery/runtime spine |
| `zf project review-spine propose` | Create a pending proposal from a spine review corrective action |

## `zf projection`

Inspect and rebuild read models

| Command | Parser description |
|---|---|
| `zf projection` | Inspect and rebuild read models |
| `zf projection doctor` | Diagnose read-model freshness and schema |
| `zf projection rebuild` | Rebuild read model from events.jsonl |
| `zf projection status` | Show read-model status |

## `zf recover`

Recover deterministic runtime state from append-only events

| Command | Parser description |
|---|---|
| `zf recover` | Recover deterministic runtime state from append-only events |
| `zf recover fanout-terminal` | Preview or append a narrow fanout child terminal recovery |
| `zf recover task-impl` | Preview or request retry after a blocked Impl repair |
| `zf recover workflow` | Inspect or resume pending workflow stage handoffs |

## `zf refs`

Verify ZaoFu git refs

| Command | Parser description |
|---|---|
| `zf refs` | Verify ZaoFu git refs |
| `zf refs verify` | Verify task and candidate refs |

## `zf report`

Generate read-only reports

| Command | Parser description |
|---|---|
| `zf report` | Generate read-only reports |
| `zf report goal-dossier` | Generate a run-scoped Goal Dossier projection and markdown report |
| `zf report hermes-run` | Deprecated alias for run-closeout with a Hermes default title |
| `zf report run-closeout` | Generate a generic run closeout markdown report |

## `zf restart`

Restart the harness or a single role

| Command | Parser description |
|---|---|
| `zf restart` | Restart the harness or a single role |

## `zf result`

Durable call-result commands

| Command | Parser description |
|---|---|
| `zf result` | Durable call-result commands |
| `zf result submit` | Submit one operation semantic result |
| `zf result validate` | Preflight one profiled result without consuming submit capability |

## `zf rules`

List active verification rules

| Command | Parser description |
|---|---|
| `zf rules` | List active verification rules |
| `zf rules promoted` | List promoted rules only |

## `zf runs`

Inspect and reconcile run archives

| Command | Parser description |
|---|---|
| `zf runs` | Inspect and reconcile run archives |
| `zf runs cancel` | Cancel an active or queued Run |
| `zf runs explain` | Explain run/stage/attempt state from shadow spine projections (131-P0) |
| `zf runs for-task` | List runs for a task id |
| `zf runs list` | List projected runs |
| `zf runs pause` | Pause new dispatch for one admitted Run |
| `zf runs rebuild` | Rebuild run projections |
| `zf runs reconcile` | Reconcile stale active runs |
| `zf runs resume` | Resume a paused Run |

## `zf self-eval`

Run deterministic self-eval contracts

| Command | Parser description |
|---|---|
| `zf self-eval` | Run deterministic self-eval contracts |
| `zf self-eval run` | Run a self-eval YAML contract |
| `zf self-eval validate` | Validate a self-eval YAML contract |

## `zf self-repair`

Run authorized harness self-repair (dispatch_requested consumer)

| Command | Parser description |
|---|---|
| `zf self-repair` | Run authorized harness self-repair (dispatch_requested consumer) |
| `zf self-repair run` | Prepare/dispatch pending self-repair requests |

## `zf skills`

Inspect configured skills

| Command | Parser description |
|---|---|
| `zf skills` | Inspect configured skills |
| `zf skills doctor` | Check enabled skill health |
| `zf skills list` | List enabled role skills |

## `zf spec`

Convert a structured spec markdown into ZaoFu kanban tasks

| Command | Parser description |
|---|---|
| `zf spec` | Convert a structured spec markdown into ZaoFu kanban tasks |
| `zf spec ingest` | Ingest spec frontmatter into kanban + events |
| `zf spec merge` | Merge an externally-produced frontmatter JSON into a plain-md spec |
| `zf spec prompt` | Print a backend-agnostic LLM prompt for extracting frontmatter |
| `zf spec validate` | Validate spec frontmatter without writing state (pre-emit gate) |

## `zf start`

Start the harness loop

| Command | Parser description |
|---|---|
| `zf start` | Start the harness loop |

## `zf state`

Inspect or clean runtime state

| Command | Parser description |
|---|---|
| `zf state` | Inspect or clean runtime state |
| `zf state clean` | Clean rebuildable runtime projections |
| `zf state reconcile` | Detect Kanban and tmux pane desync (in_progress without a live worker) |
| `zf state retention-plan` | Inventory retention classes and safe reclaim candidates (read-only) |

## `zf status`

Show current state overview

| Command | Parser description |
|---|---|
| `zf status` | Show current state overview |

## `zf stop`

Stop the harness loop

| Command | Parser description |
|---|---|
| `zf stop` | Stop the harness loop |

## `zf task`

Task-level tools

| Command | Parser description |
|---|---|
| `zf task` | Task-level tools |
| `zf task artifacts` | List artifact occurrences linked to one task |
| `zf task create-from-contract` | Atomically create feature/task/contract and optionally assign it |
| `zf task trace` | Reconstruct task lifecycle |

## `zf task-doc`

Task Capsule utilities

| Command | Parser description |
|---|---|
| `zf task-doc` | Task Capsule utilities |
| `zf task-doc ingest` | Ingest controlled task.md changes |
| `zf task-doc verify` | Verify task capsule freshness |

## `zf trace`

Inspect event traces

| Command | Parser description |
|---|---|
| `zf trace` | Inspect event traces |
| `zf trace delivery` | Feature-level idea->ship delivery trace |
| `zf trace drift` | Planned-vs-actual drift report |
| `zf trace execution-graph` | Planned task-map joined with actual runtime |
| `zf trace export` | Export Delivery telemetry or a Goal completion receipt |
| `zf trace gantt` | Per-dev swim-lane Gantt + dep DAG as Mermaid markdown |
| `zf trace operation` | Show dispatch-scoped operation timeline |
| `zf trace record-fixture` | Record a fanout replay fixture |
| `zf trace replay-fixture` | Replay a fanout fixture |
| `zf trace report` | Delivery completion report (post-mortem) by feature_id |
| `zf trace show` | Show a trace by correlation, event, or task id |
| `zf trace spans` | Project events.jsonl into span records (OBS-SPAN-001) |
| `zf trace task-node` | Single task node: planned vs actual + drift |
| `zf trace workflow-operation` | Show stable workflow-operation and call-result timeline |
| `zf trace workflow-run` | Aggregate one fanout/workflow run by fanout_id |

## `zf update`

Refresh ZaoFu-managed files (AGENTS.md managed block, etc.)

| Command | Parser description |
|---|---|
| `zf update` | Refresh ZaoFu-managed files (AGENTS.md managed block, etc.) |
| `zf update agents-md` | Update AGENTS.md kernel-managed block (<!-- ZF:START/END -->) |

## `zf validate`

Validate zf.yaml configuration

| Command | Parser description |
|---|---|
| `zf validate` | Validate zf.yaml configuration |

## `zf watch`

Tail .zf/events.jsonl with filtering

| Command | Parser description |
|---|---|
| `zf watch` | Tail .zf/events.jsonl with filtering |

## `zf web`

Start a local Web dashboard for the current .zf project

| Command | Parser description |
|---|---|
| `zf web` | Start a local Web dashboard for the current .zf project |

## `zf workdir`

Manage runtime workdirs

| Command | Parser description |
|---|---|
| `zf workdir` | Manage runtime workdirs |
| `zf workdir repair` | Repair a configured workdir |

## `zf workflow`

Inspect workflow topology

| Command | Parser description |
|---|---|
| `zf workflow` | Inspect workflow topology |
| `zf workflow audit` | Audit task workflow completeness (required_events, stage_order) |
| `zf workflow gates` | Render the effective read-only gate projection |
| `zf workflow hooks` | Render the effective read-only hook registry |
| `zf workflow inspect` | Preflight inspect workflow graph, handoff, affinity, and skills |
| `zf workflow render` | Render linear and star topology |
| `zf workflow routes` | List active workflow routes bound to a Task |
| `zf workflow start` | Preview, propose, or apply a Task-bound workflow route |

## `zf workspace`

Inspect or update local workspace metadata

| Command | Parser description |
|---|---|
| `zf workspace` | Inspect or update local workspace metadata |
| `zf workspace providers` | Manage workspace provider bindings |
| `zf workspace providers openclaw` | Manage OpenClaw bindings |
| `zf workspace providers openclaw list` | List OpenClaw bindings |
| `zf workspace providers openclaw set` | Create or update an OpenClaw binding |
