// Shared helpers/components extracted verbatim from App.tsx (P1 split).
import type { PageId, ParsedEventFilter, NewTaskDraft, AddAgentDraft, ProjectionKind, EmptyStateSpec, ProjectionMetricSpec } from "./sharedTypes";
import type { ActionResponse, ChannelSummary, EventRecord, ExecutionRouteProjection, Snapshot, Task, TraceSummary } from "../api/types";
import type { AgentConversation, AgentSessionCard } from "../components/agent-session/types";
import { RouteSummaryStrip } from "../components/kanban/TaskCard";
import { formatTime } from "../lib/format";
import { routeStatusTone } from "../lib/task-display";
import { Archive, ChevronRight, Inbox, Route } from "lucide-react";
import { useMemo } from "react";
import type { ReactNode } from "react";
import { isObservabilitySnapshotPage } from "./pageLoadPolicy";

export function isObservabilityPage(page: PageId): boolean {
  return isObservabilitySnapshotPage(page);
}


export function allBoardTasks(snapshot: Snapshot): Task[] {
  const byId = new Map<string, Task>();
  for (const task of snapshot.tasks) {
    byId.set(task.id, task);
  }
  for (const task of snapshot.archive_tasks) {
    if (task.status === "done" || task.status === "cancelled") {
      byId.set(task.id, task);
    }
  }
  return [...byId.values()];
}


export function formatUsd(value: number | undefined): string {
  return `$${(value ?? 0).toFixed(4)}`;
}


export function stringify(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}


export function redactDisplayValue(value: unknown, keyHint = ""): unknown {
  const sensitiveKey = /token|secret|api[_-]?key|authorization|password|credential|bearer|x-zf-web-token/i.test(keyHint);
  if (sensitiveKey && value !== null && value !== undefined && value !== "") return "[redacted]";
  if (typeof value === "string") {
    return value
      .replace(/(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}/gi, "$1[redacted]")
      .replace(/(sk-[A-Za-z0-9._-]{8,})/g, "[redacted]")
      .replace(/([A-Za-z0-9_]*(?:TOKEN|SECRET|API_KEY)[A-Za-z0-9_]*=)[^\s]+/g, "$1[redacted]");
  }
  if (Array.isArray(value)) return value.map((item) => redactDisplayValue(item, keyHint));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      out[key] = redactDisplayValue(item, key);
    }
    return out;
  }
  return value;
}


export function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}


export function projectLabelFromId(projectId: string): string {
  const trimmed = projectId.trim();
  if (!trimmed) return "";
  return trimmed.replace(/-[0-9a-f]{8}$/i, "") || trimmed;
}


export function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}


export function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}


export function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => textValue(item)).filter(Boolean);
}


export function parseEventFilter(value: string): ParsedEventFilter {
  const parsed: ParsedEventFilter = { unknown: [] };
  for (const token of value.trim().split(/\s+/).filter(Boolean)) {
    const separator = token.indexOf(":");
    const key = separator > 0 ? token.slice(0, separator) : "";
    const tokenValue = separator > 0 ? token.slice(separator + 1) : token;
    if (!tokenValue) continue;
    if (key === "task") parsed.task = tokenValue;
    else if (key === "actor") parsed.actor = tokenValue;
    else if (key === "type") parsed.type = tokenValue;
    else if (key === "prefix") parsed.prefix = tokenValue;
    else if (token === "failed") parsed.failed = true;
    else if (token === "blocked") parsed.blocked = true;
    else parsed.unknown.push(token);
  }
  return parsed;
}


export function eventFamily(type: string): string {
  if (type.startsWith("kanban.agent.")) return "kanban";
  if (type.startsWith("runtime.action.")) return "runtime";
  if (type.startsWith("web.action.")) return "web";
  if (type.startsWith("channel.")) return "channel";
  if (type.includes("failed") || type.includes("blocked")) return "risk";
  if (type.startsWith("worker.")) return "worker";
  return "system";
}


export function eventPayload(event: EventRecord | null): Record<string, unknown> {
  const payload = event?.payload;
  return payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
}


export function truncateInline(value: string, limit = 120): string {
  return value.length > limit ? `${value.slice(0, limit - 1)}...` : value;
}


export function eventTargetLabel(event: EventRecord): string {
  const payload = eventPayload(event);
  return (
    eventChannelId(event)
    || event.task_id
    || textValue(payload.target)
    || textValue(payload.target_ref)
    || textValue(payload.channel_id)
    || textValue(payload.task_id)
    || "-"
  );
}


export function eventSummary(event: EventRecord): string {
  const payload = eventPayload(event);
  const status = textValue(payload.status);
  const action = textValue(payload.action || payload.pattern_id || payload.kind);
  const message = textValue(payload.message || payload.text || payload.reason || payload.summary);
  const target = eventTargetLabel(event);
  const parts = [action, status].filter(Boolean);
  if (target && target !== "-") parts.push(target);
  if (message) parts.push(truncateInline(message, 96));
  return parts.length ? parts.join(" · ") : event.type;
}


export function eventMetadataChips(event: EventRecord, channelId: string, target: string): Array<{ label: string; value: string }> {
  const payload = eventPayload(event);
  const seen = new Set<string>();
  const rows: Array<{ label: string; value: string }> = [];
  const push = (label: string, value: unknown) => {
    const text = textValue(value).trim();
    if (!text || text === "-") return;
    const key = `${label}:${text}`;
    if (seen.has(key)) return;
    seen.add(key);
    rows.push({ label, value: truncateInline(text.replace(/\s+/g, " "), 72) });
  };
  push("status", payload.status);
  push("task", event.task_id || payload.task_id);
  push("channel", channelId);
  if (!channelId) push("target", target);
  push("trace", payload.trace_id);
  push("run", payload.run_id);
  push("backend", payload.backend || payload.provider);
  push("seq", event.seq);
  return rows.slice(0, 5);
}


export function actionFailed(value: unknown): value is ActionResponse {
  const record = recordValue(value);
  return record?.ok === false;
}


export function actionFailureReason(value: unknown): string {
  const record = recordValue(value);
  return textValue(record?.reason || record?.status || "action failed") || "action failed";
}


export function emptyNewTaskDraft(): NewTaskDraft {
  return {
    title: "",
    behavior: "",
    verification: "",
    assignedTo: "",
    assigneeType: "none",
    assigneeId: "",
    assigneeLabel: "",
    assigneeBackend: "",
    assigneeSupervisor: "",
    skills: "",
    blockedBy: "",
    priority: "3",
  };
}


export function emptyAddAgentDraft(): AddAgentDraft {
  return {
    memberId: "",
    memberType: "provider_agent",
    provider: "codex",
    providerBindingId: "",
    channelRole: "tech_leader",
    visibilityProfile: "planner",
    roleContextRef: "channel_roles/tech-leader.md",
    skillRefs: "",
    backend: "codex",
    scope: "channel",
    reason: "added from channel detail",
    permissionProfile: "project_writer",
    dangerousAck: false,
    canMessage: true,
    canSummarize: true,
    canProposeWorkflow: true,
  };
}


export function csvList(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}


export function agentConversationScrollSignature(
  conversation: AgentConversation,
  activeThreadId: string,
  extraCards: AgentSessionCard[],
): string {
  const thread = conversation.threads.find((item) => item.id === activeThreadId)
    ?? conversation.threads[0];
  if (!thread) {
    return `${conversation.id}:${activeThreadId}:empty:${extraCards.length}`;
  }
  const turn = thread.turns[thread.turns.length - 1];
  const run = turn?.runs[turn.runs.length - 1];
  const part = run?.parts[run.parts.length - 1];
  const cardSignature = extraCards
    .map((card) => `${card.id}:${card.status ?? ""}:${(card.body ?? "").length}`)
    .join(",");
  return [
    conversation.id,
    activeThreadId,
    thread.id,
    thread.status,
    thread.activeRunId ?? "",
    thread.turns.length,
    turn?.id ?? "",
    turn?.user?.id ?? "",
    (turn?.user?.content ?? "").length,
    run?.id ?? "",
    run?.status ?? "",
    run?.parts.length ?? 0,
    run?.updatedAt ?? "",
    part?.id ?? "",
    part?.state ?? "",
    part?.seq ?? "",
    part?.updatedAt ?? "",
    (part?.content ?? "").length,
    (part?.summary ?? "").length,
    cardSignature,
  ].join("|");
}


export function scrollElementToBottom(node: HTMLElement | null): void {
  if (!node) return;
  const apply = () => {
    node.scrollTop = node.scrollHeight;
  };
  window.requestAnimationFrame(() => {
    apply();
    window.requestAnimationFrame(apply);
  });
}


export function recordValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}


export function channelIdOf(channel: ChannelSummary | null | undefined): string {
  return String(channel?.channel_id ?? (channel as Record<string, unknown> | null | undefined)?.id ?? "");
}


export function channelNameOf(channel: ChannelSummary | null | undefined): string {
  const id = channelIdOf(channel);
  return String(channel?.name ?? (id ? `# ${id}` : "# zaofu"));
}


export function recordString(row: Record<string, unknown>, key: string, fallback = ""): string {
  const value = row[key];
  if (value === null || value === undefined) return fallback;
  return String(value);
}


export function KeyValuePanel({ rows, title }: { rows: Array<{ key: string; value: unknown }>; title: string }) {
  return (
    <div className="subsection key-panel">
      <div className="key-panel-heading">
        <h3>{title}</h3>
        <span className="muted">{rows.length} fields</span>
      </div>
      <dl className="detail-grid compact-detail-grid key-value-fields">
        {rows.map((row) => (
          <FragmentRow key={row.key} name={row.key} value={row.value} />
        ))}
      </dl>
    </div>
  );
}


export function FragmentRow({ name, value }: { name: string; value: unknown }) {
  const displayValue = displayValueForKey(name, value);
  const rawValue = stringify(value);
  const monoValue = isPathLikeKey(name) || name.includes("id");
  return (
    <>
      <dt>{name}</dt>
      <dd className={monoValue ? "mono" : ""} title={rawValue}>
        {displayValue}
      </dd>
    </>
  );
}


export function isPathLikeKey(name: string): boolean {
  const normalized = name.toLowerCase();
  return ["dir", "repo", "root", "path", "file"].some((token) => normalized.includes(token));
}


export function displayValueForKey(name: string, value: unknown): string {
  const rawValue = stringify(value);
  if (!isPathLikeKey(name)) return rawValue;
  if (!rawValue.includes("/") && !rawValue.includes("\\")) return rawValue;
  return compactPath(rawValue);
}


export function formatAge(value: number | null): string {
  if (value == null) return "unknown";
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  return `${Math.round(value / 3600)}h`;
}



export function ExecutionRoutePanel({
  error,
  loading,
  onOpenProjection,
  route,
  traceId,
}: {
  error?: string | null;
  loading?: boolean;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  route: ExecutionRouteProjection | null;
  traceId: string;
}) {
  if (loading && !route) {
    return (
      <div className="subsection route-panel">
        <div className="inline-heading">
          <h3>Execution Route</h3>
          <span className="muted">loading timeline</span>
        </div>
        <p className="empty-text">Loading route events...</p>
      </div>
    );
  }

  if (error && !route) {
    return (
      <div className="subsection route-panel">
        <div className="inline-heading">
          <h3>Execution Route</h3>
          <span className="badge badge-failed">error</span>
        </div>
        <p className="empty-text">Timeline unavailable: {error}</p>
      </div>
    );
  }

  if (!route || route.empty) {
    return (
      <div className="subsection route-panel">
        <div className="inline-heading">
          <h3>Execution Route</h3>
          <span className="muted">events projection</span>
        </div>
        <p className="empty-text">No route events yet.</p>
      </div>
    );
  }

  return (
    <div className="route-panel">
      <div className="subsection route-summary-panel">
        <div className="inline-heading">
          <h3>Execution Route</h3>
          <div className="button-row">
            <span className={`badge badge-${routeStatusTone(route.status)}`}>{route.status}</span>
            {traceId ? (
              <button
                className="icon-button"
                type="button"
                onClick={() => onOpenProjection("trace", traceId)}
              >
                Open Trace
              </button>
            ) : null}
          </div>
        </div>
        <RouteSummaryStrip route={route} />
      </div>
      <div className="route-layout">
        <section className="subsection">
          <div className="inline-heading">
            <h3>Linear Timeline</h3>
            <span className="muted">{route.linear.length} stages</span>
          </div>
          <LinearRouteTimeline route={route} />
        </section>
        <section className="subsection">
          <div className="inline-heading">
            <h3>DAG</h3>
            <span className="muted">{route.dag.nodes.length} nodes · {route.dag.edges.length} edges</span>
          </div>
          <RouteDAGView route={route} />
        </section>
      </div>
      <section className="subsection">
        <div className="inline-heading">
          <h3>Swimlanes</h3>
          <span className="muted">{route.swimlanes.length} actors</span>
        </div>
        <RouteSwimlanes route={route} />
      </section>
    </div>
  );
}


export function LinearRouteTimeline({ route }: { route: ExecutionRouteProjection }) {
  return (
    <ol className="route-timeline">
      {route.linear.map((stage) => (
        <li className={`route-timeline-item status-${stage.status}`} key={`${stage.stage}-${stage.first_seq}`}>
          <span className={`route-dot status-${stage.status}`} />
          <div className="route-timeline-body">
            <div className="inline-heading">
              <h3>{stage.label}</h3>
              <span className={`badge badge-${routeStatusTone(stage.status)}`}>{stage.status}</span>
            </div>
            <div className="route-actor-row">
              {stage.actors.length ? stage.actors.map((actor) => (
                <span className="route-actor-chip mono" key={`${stage.stage}-${actor}`}>{actor}</span>
              )) : <span className="muted">system</span>}
            </div>
            <div className="route-meta-row">
              <span>{stage.event_count} events</span>
              <span>{formatTime(stage.last_ts)}</span>
              {stage.parallel ? <span>parallel</span> : null}
              {stage.failed_count ? <span className="route-failed">failed {stage.failed_count}</span> : null}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}


export function RouteDAGView({ route }: { route: ExecutionRouteProjection }) {
  const nodeById = new Map(route.dag.nodes.map((node) => [node.id, node]));
  return (
    <div className="route-dag">
      {route.linear.map((stage, index) => (
        <div className="route-dag-stage" key={`${stage.stage}-${stage.first_seq}`}>
          <div className="route-dag-stage-title">
            <span>{stage.label}</span>
            <span className="muted">{stage.node_ids.length}</span>
          </div>
          <div className="route-dag-nodes">
            {stage.node_ids.map((nodeId) => {
              const node = nodeById.get(nodeId);
              if (!node) return null;
              return (
                <div className={`route-dag-node status-${node.status}`} key={node.id}>
                  <span className="mono">{node.actor}</span>
                  <span>{node.stage_label}</span>
                  <span className={`badge badge-${routeStatusTone(node.status)}`}>{node.status}</span>
                </div>
              );
            })}
          </div>
          {index < route.linear.length - 1 ? <span className="route-dag-arrow">→</span> : null}
        </div>
      ))}
    </div>
  );
}


export function RouteSwimlanes({ route }: { route: ExecutionRouteProjection }) {
  if (!route.swimlanes.length) {
    return <p className="empty-text route-empty">No swimlane data.</p>;
  }
  return (
    <div className="route-swimlanes">
      {route.swimlanes.map((lane) => (
        <div className="route-swimlane" key={lane.actor}>
          <span className="route-swimlane-actor mono">{lane.actor}</span>
          <div className="route-swimlane-items">
            {lane.items.map((item, index) => (
              <span
                className={`route-swimlane-item status-${textValue(item.status)}`}
                key={`${lane.actor}-${textValue(item.node_id)}-${index}`}
              >
                {textValue(item.stage)}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}


export function ProjectionEmptyState({ state }: { state: EmptyStateSpec }) {
  const Icon = state.icon ?? Inbox;
  return (
    <div className={`projection-empty-state ${state.compact ? "compact" : ""}`}>
      <span className="projection-empty-icon" aria-hidden="true">
        <Icon size={state.compact ? 16 : 20} strokeWidth={1.8} />
      </span>
      <div className="projection-empty-copy">
        <strong>{state.title}</strong>
        <p>{state.description}</p>
      </div>
      {state.actions?.length ? (
        <div className="projection-empty-actions">
          {state.actions.map((action) => (
            <button
              className="icon-button"
              key={action.label}
              type="button"
              onClick={action.onClick}
              disabled={!action.onClick}
            >
              {action.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}


export function ProjectionList({
  detail,
  emptyState,
  idKey,
  onOpen,
  rows,
  title,
}: {
  detail: Record<string, unknown> | null;
  emptyState?: EmptyStateSpec;
  idKey: string;
  onOpen: (id: string) => void;
  rows: object[];
  title: string;
}) {
  return (
    <>
      <TablePage title={title} rows={rows} onOpen={(row) => onOpen(String(row[idKey] ?? ""))} emptyState={emptyState} />
      {detail ? (
        <div className="subsection">
          <h3>Detail</h3>
          {idKey === "fanout_id" ? (
            <FanoutDetailPanel detail={detail} />
          ) : idKey === "run_id" ? (
            <RunDetailPanel detail={detail} />
          ) : idKey === "trace_id" ? (
            <TraceDetailPanel detail={detail} />
          ) : (
            <PreBlock value={detail} />
          )}
        </div>
      ) : null}
    </>
  );
}


export function TraceDetailPanel({ detail }: { detail: Record<string, unknown> }) {
  const route = asExecutionRoute(detail.execution_route);
  const timeline = Array.isArray(detail.timeline)
    ? (detail.timeline as EventRecord[])
    : [];
  const taskIds = Array.isArray(detail.tasks) ? detail.tasks.map(String) : [];
  const actors = Array.isArray(detail.actors) ? detail.actors.map(String) : [];
  const rows = [
    { key: "trace", value: textValue(detail.trace_id) },
    { key: "events", value: Number(detail.event_count ?? timeline.length) },
    { key: "tasks", value: taskIds.join(", ") || "-" },
    { key: "actors", value: actors.join(", ") || "-" },
    { key: "route", value: route?.summary || "-" },
  ];
  return (
    <div className="trace-detail-panel">
      <KeyValuePanel title="Trace Summary" rows={rows} />
      {route ? (
        <ExecutionRoutePanel
          route={route}
          traceId=""
          onOpenProjection={() => undefined}
        />
      ) : (
        <p className="empty-text">No route projection for this trace.</p>
      )}
      <div className="subsection">
        <div className="inline-heading">
          <h3>Trace Events</h3>
          <span className="muted">{timeline.length}</span>
        </div>
        <EventTable events={timeline.slice(-80)} compact />
      </div>
    </div>
  );
}


export function asExecutionRoute(value: unknown): ExecutionRouteProjection | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const route = value as Partial<ExecutionRouteProjection>;
  if (!Array.isArray(route.linear) || !route.dag || !Array.isArray(route.dag.nodes)) {
    return null;
  }
  return route as ExecutionRouteProjection;
}


export function FanoutDetailPanel({ detail }: { detail: Record<string, unknown> }) {
  const progress = asRecord(detail.progress);
  const laneProjection = asRecord(detail.lane_projection);
  const trigger = asRecord(detail.trigger);
  const aggregate = asRecord(detail.aggregate);
  const children = Array.isArray(detail.children)
    ? detail.children.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const hasLaneProjection = Boolean(
    laneProjection.strategy
      || laneProjection.planned_lane_count
      || laneProjection.active_child_count,
  );
  return (
    <div className="fanout-panel">
      <dl className="detail-grid">
        <dt>Fanout</dt>
        <dd className="mono">{stringify(detail.fanout_id)}</dd>
        <dt>Topology</dt>
        <dd>{stringify(detail.topology)}</dd>
        <dt>Target</dt>
        <dd className="mono">{stringify(detail.target_ref)}</dd>
        <dt>Progress</dt>
        <dd>{stringify(progress.done)}/{stringify(progress.total)}</dd>
        {hasLaneProjection ? (
          <>
            <dt>Lanes</dt>
            <dd>
              planned {stringify(laneProjection.planned_lane_count)} / active {stringify(laneProjection.active_child_count)}
              {laneProjection.scope ? ` (${stringify(laneProjection.scope)})` : ""}
            </dd>
          </>
        ) : null}
        <dt>Requested</dt>
        <dd>{stringify(trigger.requested_by)}</dd>
        <dt>Triggered</dt>
        <dd>{stringify(trigger.triggered_by)}</dd>
        <dt>Started</dt>
        <dd>{stringify(trigger.started_by)}</dd>
        <dt>Aggregate</dt>
        <dd>{stringify(aggregate.status)} {aggregate.mode ? `(${stringify(aggregate.mode)})` : ""}</dd>
      </dl>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Child</th>
              <th>Role</th>
              <th>Status</th>
              <th>Task</th>
              <th>Task Status</th>
              <th>Run</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {children.map((child, index) => (
              <FanoutChildRow child={child} index={index} key={`${stringify(child.child_id)}-${index}`} />
            ))}
          </tbody>
        </table>
      </div>
      {children.length === 0 ? <p className="empty-text">No child projection data.</p> : null}
    </div>
  );
}


export function FanoutChildRow({ child, index }: { child: Record<string, unknown>; index: number }) {
  const linkedTask = asRecord(child.linked_task);
  return (
    <tr key={`${stringify(child.child_id)}-${index}`}>
      <td className="mono">{stringify(child.child_id ?? child.id)}</td>
      <td>{stringify(child.role_instance)}</td>
      <td>{stringify(child.status)}</td>
      <td className="mono">{stringify(child.task_id)}</td>
      <td>{stringify(linkedTask.task_status ?? linkedTask.kanban_column ?? "")}</td>
      <td className="mono">{stringify(child.run_id)}</td>
      <td>{stringify(child.recommendation ?? child.reason ?? child.task_ref ?? child.source_commit)}</td>
    </tr>
  );
}


export function RunDetailPanel({ detail }: { detail: Record<string, unknown> }) {
  const run = asRecord(detail.run);
  const manifest = asRecord(detail.manifest);
  const artifacts = Array.isArray(detail.artifacts)
    ? detail.artifacts.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  return (
    <div className="fanout-panel">
      <dl className="detail-grid">
        <dt>Run</dt>
        <dd className="mono">{stringify(detail.run_id)}</dd>
        <dt>Status</dt>
        <dd>{stringify(run.status ?? manifest.status)}</dd>
        <dt>Scenario</dt>
        <dd>{stringify(run.scenario_id)}</dd>
        <dt>Task</dt>
        <dd className="mono">{stringify(run.test_task_id)}</dd>
        <dt>Trace</dt>
        <dd className="mono">{stringify(run.trace_id)}</dd>
        <dt>Archive</dt>
        <dd className="mono">{stringify(detail.artifact_dir)}</dd>
        <dt>Missing</dt>
        <dd>{Array.isArray(manifest.missing) ? manifest.missing.length : 0}</dd>
        <dt>Redacted</dt>
        <dd>{Array.isArray(manifest.redacted) ? manifest.redacted.length : 0}</dd>
      </dl>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Artifact</th>
              <th>Kind</th>
              <th>Bytes</th>
              <th>Missing</th>
              <th>Redacted</th>
            </tr>
          </thead>
          <tbody>
            {artifacts.slice(0, 80).map((artifact, index) => (
              <tr key={`${stringify(artifact.path)}-${index}`}>
                <td className="mono">{stringify(artifact.path)}</td>
                <td>{stringify(artifact.kind)}</td>
                <td>{stringify(artifact.bytes)}</td>
                <td>{stringify(artifact.missing)}</td>
                <td>{stringify(artifact.redacted)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {artifacts.length === 0 ? <p className="empty-text">No archived artifact manifest.</p> : null}
    </div>
  );
}


export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}


export function ProjectionMetricGrid({
  className = "",
  metrics,
}: {
  className?: string;
  metrics: ProjectionMetricSpec[];
}) {
  return (
    <div className={`projection-summary-grid ${className}`.trim()}>
      {metrics.map((metric) => {
        const Icon = metric.icon;
        return (
          <div className={`projection-summary-card tone-${metric.tone ?? "muted"}`} key={metric.label}>
            <span className="projection-summary-label">
              {Icon ? <Icon size={14} strokeWidth={1.9} aria-hidden="true" /> : null}
              {metric.label}
            </span>
            <strong>{metric.value}</strong>
            <small>{metric.meta}</small>
          </div>
        );
      })}
    </div>
  );
}


export function TraceIndexList({
  onOpen,
  rows,
}: {
  onOpen: (id: string) => void;
  rows: TraceSummary[];
}) {
  return (
    <div className="trace-index-panel">
      <div className="inline-heading">
        <h3>Trace Index</h3>
        <span className="muted">{rows.length} traces</span>
      </div>
      <div className="trace-index-list">
        {rows.map((row) => {
          const traceId = textValue(row.trace_id);
          const taskCount = Array.isArray(row.task_ids) ? row.task_ids.length : 0;
          const actorCount = Array.isArray(row.actors) ? row.actors.length : 0;
          return (
            <button className="trace-index-card" key={traceId} type="button" onClick={() => onOpen(traceId)}>
              <div className="trace-index-card-head">
                <span className="badge badge-info">{row.event_count} events</span>
                <strong className="mono">{automationShortId(traceId)}</strong>
              </div>
              <p>{textValue(row.last_type) || "trace projection"}</p>
              <div className="trace-index-meta">
                <span>seq {row.first_seq}-{row.last_seq}</span>
                {taskCount ? <span>{taskCount} tasks</span> : null}
                {actorCount ? <span>{actorCount} actors</span> : null}
                {row.last_ts ? <span>{formatTime(row.last_ts)}</span> : null}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}


export function compactPath(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  if (trimmed.length <= 46) return trimmed;
  const parts = trimmed.split("/").filter(Boolean);
  if (parts.length >= 3) return `.../${parts.slice(-3).join("/")}`;
  return `${trimmed.slice(0, 16)}...${trimmed.slice(-24)}`;
}


export function automationStatusTone(status: string): "ok" | "warn" | "err" | "info" {
  const normalized = status.toLowerCase();
  if (normalized === "completed" || normalized === "passed" || normalized === "success") return "ok";
  if (normalized === "failed" || normalized === "error") return "err";
  if (normalized === "skipped" || normalized === "cancelled" || normalized === "canceled") return "warn";
  return "info";
}


export function automationShortId(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "-";
  if (trimmed.length <= 16) return trimmed;
  return `${trimmed.slice(0, 8)}…${trimmed.slice(-4)}`;
}


export function automationShortRunId(value: string): string {
  const match = value.match(/(\d{8}T\d{6}Z)-([A-Za-z0-9]+)$/);
  if (match) return `${match[1]} · ${match[2]}`;
  return automationShortId(value);
}


export function needsOperatorAttention(value?: string | null): boolean {
  return !["", "idle", "working", "completed_verified"].includes(value ?? "");
}


export function supportLabel(value: unknown): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "unknown";
}


export function TablePage({
  embedded = false,
  emptyState,
  onOpen,
  rows,
  title,
}: {
  embedded?: boolean;
  emptyState?: EmptyStateSpec;
  onOpen?: (row: Record<string, unknown>) => void;
  rows: object[];
  title: string;
}) {
  const keys = useMemo(() => {
    const first = (rows[0] ?? {}) as Record<string, unknown>;
    return Object.keys(first).slice(0, 8);
  }, [rows]);
  const effectiveEmptyState: EmptyStateSpec = emptyState ?? {
    title: `No ${title.toLowerCase()} rows`,
    description: "This read-only projection has no rows for the current project yet.",
    icon: Inbox,
    compact: embedded,
  };

  return (
    <div className={embedded ? "subsection" : ""}>
      <div className={embedded ? "inline-heading" : "section-heading"}>
        <div>
          <h2>{title}</h2>
          <span className="muted">{rows.length} rows</span>
        </div>
      </div>
      {rows.length === 0 ? (
        <ProjectionEmptyState state={{ ...effectiveEmptyState, compact: effectiveEmptyState.compact ?? embedded }} />
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                {keys.map((key) => <th key={key}>{key}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((rawRow, index) => {
                const row = rawRow as Record<string, unknown>;
                return (
                  <tr key={index} onClick={() => onOpen?.(row)}>
                    {keys.map((key) => {
                      const text = stringify(row[key]);
                      return (
                        <td
                          key={key}
                          data-label={key}
                          title={text}
                          className={key.includes("id") || key.includes("ref") ? "mono" : ""}
                        >
                          {text.slice(0, 160)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


export function RuntimeSummaryCard({
  label,
  meta,
  tone = "muted",
  value,
}: {
  label: string;
  meta: string;
  tone?: "ok" | "warn" | "err" | "info" | "muted";
  value: string | number;
}) {
  return (
    <div className={`runtime-summary-card tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{meta}</small>
    </div>
  );
}


export function RuntimeDetailSection({
  children,
  defaultOpen = false,
  meta,
  title,
}: {
  children: ReactNode;
  defaultOpen?: boolean;
  meta: string;
  title: string;
}) {
  return (
    <details className="runtime-detail-section" open={defaultOpen}>
      <summary>
        <span>{title}</span>
        <small>{meta}</small>
        <ChevronRight className="rail-section-chevron" size={15} strokeWidth={1.9} aria-hidden="true" />
      </summary>
      <div className="runtime-detail-body">
        {children}
      </div>
    </details>
  );
}


export function eventKey(event: EventRecord, index: number): string {
  return `${event.seq ?? ""}:${event.id ?? ""}:${index}`;
}


export function eventChannelId(event: EventRecord | null): string {
  const payload = asRecord(event?.payload);
  const direct = textValue(payload.channel_id);
  if (direct) return direct;
  const scope = asRecord(payload.scope);
  const scoped = textValue(scope.channel_id);
  if (scoped) return scoped;
  const correlation = textValue(event?.correlation_id);
  return correlation.startsWith("ch-") || correlation === "zaofu" ? correlation : "";
}


export function EventTable({
  compact = false,
  events,
  onOpenChannel,
  onSelect,
  selectedEventKey,
}: {
  compact?: boolean;
  events: EventRecord[];
  onOpenChannel?: (channelId: string) => void;
  onSelect?: (key: string) => void;
  selectedEventKey?: string;
}) {
  return (
    <div className={compact ? "event-list compact-events" : "event-list"}>
      {events.map((event, index) => {
        const channelId = eventChannelId(event);
        const target = eventTargetLabel(event);
        const metadata = eventMetadataChips(event, channelId, target);
        const visibleMetadata = metadata.filter((chip) => !(chip.label === "channel" && channelId && onOpenChannel && !compact));
        const source = event.actor || eventFamily(event.type);
        return (
          <div
            className={`event-row event-family-${eventFamily(event.type)} ${selectedEventKey === eventKey(event, index) ? "active" : ""} ${onSelect ? "selectable" : ""}`}
            key={`${event.seq ?? ""}-${event.id ?? ""}-${index}`}
            onClick={() => onSelect?.(eventKey(event, index))}
          >
            <div className="event-row-main">
              <span className="event-family-dot" aria-hidden="true" />
              <span className="event-time muted mono">{formatTime(event.ts)}</span>
              <span className="event-summary">{eventSummary(event)}</span>
              <span className="event-source" title={source}>{source}</span>
            </div>
            <div className="event-row-meta">
              <span className="event-type-text mono" title={event.type}>{event.type}</span>
              {channelId && onOpenChannel && !compact ? (
                <button
                  className="event-target-pill link-button mono"
                  type="button"
                  onClick={(clickEvent) => {
                    clickEvent.stopPropagation();
                    onOpenChannel(channelId);
                  }}
                >
                  channel:{channelId}
                </button>
              ) : target !== "-" ? (
                <span className="event-target-pill muted mono">{target}</span>
              ) : null}
              {visibleMetadata.map((chip) => (
                <span className="event-meta-chip mono" key={`${chip.label}:${chip.value}`}>
                  {chip.label}:{chip.value}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}


export function PreBlock({ value }: { value: unknown }) {
  return <pre className="text-block">{stringify(redactDisplayValue(value))}</pre>;
}
