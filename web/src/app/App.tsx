import { uiText } from "../settings/uiText";
import { useDeveloperAccess, refreshDeveloperAccess } from "../settings/developerAccess";
import type { CSSProperties, PointerEvent, ReactNode, UIEvent as ReactUIEvent } from "react";
import { actionEventRefreshRunsInBackground, actionIsReadOnlySelfIssue } from "../api/actionPolicy";
import { Fragment, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Archive,
  ArrowUp,
  AtSign,
  Bell,
  Bot,
  Boxes,
  Bold,
  CalendarClock,
  ChevronDown,
  ChevronRight,
  Code,
  FileText,
  FolderGit2,
  Gauge,
  GitFork,
  Hash,
  Home,
  Inbox,
  Italic,
  Link,
  List,
  ListTodo,
  ListOrdered,
  Map as MapIcon,
  Maximize2,
  MessageCircle,
  MessageSquare,
  Minimize2,
  Minus,
  MoreHorizontal,
  PauseCircle,
  PlayCircle,
  Plus,
  Quote,
  Radio,
  RefreshCw,
  Search,
  Send,
  Smile,
  Settings,
  SkipBack,
  SkipForward,
  SquareCode,
  Strikethrough,
  Terminal,
  Trash2,
  Route,
  Type,
  Underline,
  Users,
  Wrench,
  X,
} from "lucide-react";
import {
  fetchOverviewPulse,
  getChannelConversation,
  getChannelDetail,
  getChannels,
  getCandidateDetail,
  getDeliveryFeatures,
  getKanbanPendingProposals,
  getFanoutDetail,
  getAgentCockpit,
  getAgentLive,
  getAgents,
  getOperatorInbox,
  getRecentEvents,
  getRecentEventsPage,
  getProjectHealth,
  getRunDetail,
  getSnapshot,
  getSnapshotLight,
  getTaskDetail,
  getTaskDiff,
  getTaskTimeline,
  getWebSession,
  getWorkspaceProjects,
  getOnboarding,
  initWorkspaceProject,
  lockWebSession,
  postAction,
  postChannelMessage,
  registerWorkspaceProject,
  removeWorkspaceProject,
  search,
  searchChannelHistory,
  touchWorkspaceProject,
  unlockWebSession,
  validateWorkspaceProjectPath,
  type WorkspaceProjectPathInspection,
} from "../api/client";
import type { PendingKanbanProposal } from "../api/client";
import { mergeAutopilotDescriptors } from "./triageProposals";
import type { AutopilotProposalDescriptor } from "./triageProposals";
import { AgentSessionTimeline } from "../components/agent-session/AgentSessionTimeline";
import { buildChannelConversation, buildKanbanConversation } from "../components/agent-session/projection";
import type { AgentConversation, AgentProviderCapability, AgentSessionActionProposal, AgentSessionCard, AgentSessionThreadRef } from "../components/agent-session/types";
import { BOARD_COLUMNS, isBoardColumnId, activeWorkflowColumn, taskColumn } from "../components/kanban/board";
import { LogsPanel } from "../components/observability/LogsPanel";
import { MetricsStrip, sparkline } from "../components/overview/MetricsStrip";
import { PulseBand } from "../components/overview/PulseBand";
import { TaskFlowBand } from "../components/overview/TaskFlowBand";
import type { BoardColumnId } from "../components/kanban/board";
import { formatTime, formatTokens, contextBadgeTone, contextLabel } from "../lib/format";
import { taskPriority, taskActorLabel, taskRiskBadge, latestEventAge, routeStatusTone } from "../lib/task-display";
import type { TaskTelemetry } from "../lib/task-display";
import { BacklogRefsBadge, RouteSummaryStrip, WorkflowBadges } from "../components/kanban/TaskCard";
import { BoardColumn } from "../components/kanban/BoardColumn";
import { buildObservabilityEventWindow } from "./observabilityModel";
import { buildProjectCostPresentation } from "./costPrecision";
import { useProjectCost } from "./useProjectCost";
import { ProjectEventBus } from "./projectEventBus";
import { pageTitle } from "./pageMetadata";
import { LatestRequestGate } from "./latestRequestGate";
import {
  mergeChannelConversationPages,
  mergeChannelConversationRefresh,
} from "./channelConversationState";
import { useProjectRequestScope } from "./useProjectRequestScope";
import { resolveWorkspaceProjectId } from "./workspaceProjectSelection";
import { ChannelRoute, OrchestratorRoute, ProjectionRoute, TracesRoute } from "./lazyRoutes";
import type { TriageTab } from "../pages/IssueTriagePage";
import { useSettingsPreferences } from '../settings/useSettingsPreferences';
import {
  selfIssueOAuthCallback,
  selfIssueOAuthSession,
  withoutSelfIssueOAuthCallback,
} from "../components/orchestrator/selfIssue";
import {
  canBootstrapScopedPageBeforeWorkspace,
  projectionPageQuery,
  projectionSelectionForPage,
  resolvePageCompatibility,
} from "./pageCompatibility";
import { isRefreshNoiseEventType } from "./refreshEventPolicy";
import { createChannelActionAdapter } from "./channelActionAdapter";
import { useChannelLiveConvergence } from "./useChannelLiveConvergence";
import { shouldRefreshChannelsAfterAction } from "./kanbanAgentInteractionPolicy";
import { useProjectObservabilityData } from "./useProjectObservabilityData";
import { useProjectStreamGapRecovery } from "./projectStreamGapRecovery";
import {
  attentionTone,
  buildAgentAttentionRows,
  buildFleetMetrics,
  buildRoleFleetRows,
  contextPercent,
  isBackendWorker,
  needsAttention,
} from "./cockpitModel";
import type {
  ActionResponse,
  AgentSummary,
  ChannelDetail,
  ChannelHistorySearchResult,
  ChannelsPage,
  ChannelSummary,
  CostSummary,
  DeliveryFeaturesPage,
  EventRecord,
  EventsPage,
  ExecutionRouteProjection,
  ExecutionPatternProjection,
  FanoutSummary,
  FleetStats,
  IntegrationQueueEntry,
  IntegrationQueueProjection,
  TaskPipelineProjection,
  MetricsSnapshotProjection,
  OnboardingStatus,
  OverviewPulse,
  RecentEvent,
  RepairActionProjection,
  RepairActionRecord,
  RuntimeSummary,
  RunSummary,
  SearchResult,
  SkillsSummary,
  Snapshot,
  Task,
  TaskDetail as TaskDetailModel,
  TaskDiff,
  TaskFlowStats,
  TaskTimeline,
  TraceSummary,
  WorkdirSummary,
  WorkspaceProject,
} from "../api/types";
import { PAGES } from "./sharedTypes";
import {
  BOARD_REFRESH_PAGES,
  MEASURE_REFRESH_PAGES,
  type BootstrapResource,
  bootstrapEventCursor,
  channelEventRefreshPlan,
  pageLoadsDeliveryFeatures,
  pageLoadsSnapshot,
  pageOwnsBootstrapResource,
  pagePollsOperatorInbox,
  projectionNeedsFresh,
  snapshotLoadKindForPage,
} from "./pageLoadPolicy";
// P1 frontend split: pages/shared extracted from this file.
import { PlanApprovalPanel } from "../components/delivery-trace/PlanApprovalPanel";
import { BoardWorkbench } from "../components/kanban/BoardWorkbench";
import { TaskDetail } from "../components/kanban/TaskDetail";
import {
  ProjectAdmissionModal,
  type ProjectAdmissionDraft,
} from "../components/workspace/ProjectAdmissionModal";
import { WorkspaceRail } from "../components/workspace/WorkspaceRail";
import { WelcomeWizard } from "../components/workspace/WelcomeWizard";
import { AddAgentModal } from "../components/modals/AddAgentModal";
import type { AddAgentDraft, AgentPanelMode, ChannelPermissionProfile, DetailTab, LiveState, OrchestratorContext, PageId, ProjectionKind, ThemeMode, UiTone, ViewMode, OperatorBackend } from "./sharedTypes";
import { KeyValuePanel, PreBlock, actionFailed, actionFailureReason, allBoardTasks, asRecord, asRecordArray, asStringArray, automationShortRunId, automationStatusTone, channelIdOf, channelNameOf, csvList, emptyAddAgentDraft, formatAge, isObservabilityPage, projectLabelFromId, recordString, recordValue, stringify, textValue } from "./shared";

const REFRESH_EVENT_TYPES = new Set(["stream.gap"]);
const LOCAL_AGENT_STREAM_EVENTS = new Set([
  "agent.session.part.delta",
  "kanban.agent.turn.delta",
  "kanban.agent.message.delta",
  "channel.message.stream.delta",
]);
const BEHAVIOR_LOOP_QUERY_KEYS = new Set(["layout", "lens", "loop_id", "stage", "node_id", "v"]);
const PAGE_QUERY_KEYS: Partial<Record<PageId, ReadonlySet<string>>> = {
  delivery: new Set(["feature"]),
  "delivery-trace": new Set(["feature"]),
  "delivery-graph": new Set(["feature"]),
  traces: new Set(["trace_id", "span_id", "obs_q", "obs_status", "obs_duration", "obs_role", "obs_backend"]),
  events: new Set(["task"]),
  runs: new Set(["run_id"]),
  fanouts: new Set(["fanout_id"]),
  candidates: new Set(["pdd_id"]),
};
const REFRESH_EVENT_PREFIXES = [
  "task.",
  "feature.",
  "dev.",
  "review.",
  "test.",
  "judge.",
  "worker.",
  "kanban.",
  "operator.",
  "candidate.",
  "fanout.",
  "channel.",
  "workflow.invoke.",
  "run.",
  "ship.",
  "runtime.action.",
  "web.action.",
  "skills.",
  "workdir.",
  "reader.",
  "user.",
  "autopilot.",
  "automation.",
  "assignment.",
  "agent.session.",
  "spine_review.",
];


interface NewChannelDraft {
  name: string;
  channelId: string;
}

const TerminalDrawer = lazy(() => import("../components/terminal/TerminalDrawer"));
const IssueTriagePage = lazy(() => import("../pages/IssueTriagePage").then((module) => ({
  default: module.IssueTriagePage,
})));

interface RuntimeActionState {
  actionReady: boolean;
  actionState: string;
  mutationEnabled: boolean;
  passcodeRequired: boolean;
  sessionActionReady: boolean;
  showTokenRow: boolean;
  tokenRequired: boolean;
}

interface HeadlessActionProposal {
  action: string;
  requestedAction: string;
  payload: Record<string, unknown>;
  reason: string;
  confidence: string;
  valid: boolean;
  validationError: string;
}

interface HeadlessThreadItem {
  key: string;
  role: "user" | "agent";
  meta: string;
  body: string;
  proposal: HeadlessActionProposal | null;
  status?: "streaming" | "completed" | "failed";
  stage?: "starting" | "thinking" | "typing" | "tool" | "completed" | "failed";
  stageLabel?: string;
  streamEvents?: string[];
}

function readInitialQuery() {
  const resolved = resolvePageCompatibility(new URLSearchParams(window.location.search));
  const params = resolved.params;
  if (resolved.changed) {
    window.history.replaceState(null, "", `?${params.toString()}`);
  }
  const view = params.get("view");
  const mobileDefaultList = window.matchMedia?.("(max-width: 680px)").matches ?? false;
  return {
    page: PAGES.includes(resolved.page as PageId) ? (resolved.page as PageId) : "board",
    view: view === "list" || (!view && mobileDefaultList) ? "list" as ViewMode : "board" as ViewMode,
    status: params.get("status") ?? "all",
    task: params.get("task"),
    channel: params.get("channel") ?? "",
    openAgent: params.get("agent") === "open",
    project: params.get("project") ?? "",
    plan: params.get("plan"),
  };
}


function snapshotAgeLabel(value?: string): string {
  if (!value) return "not generated";
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return formatTime(value) || value;
  const seconds = Math.max(0, Math.round((Date.now() - time) / 1000));
  if (seconds < 60) return `${seconds}s old`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m old`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h old`;
  return `${Math.round(hours / 24)}d old`;
}

function prependEvent(events: RecentEvent[], event: RecentEvent): RecentEvent[] {
  return [event, ...events].slice(0, 120);
}

// Control-loop churn events fire ~1/second on live projects (observed:
// task.requeue.skipped at 1 Hz on r10). Each one matches a refresh prefix and
// used to trigger a slice reload per event — a request storm that resonated
// with the server-side rebuild storm. These carry no operator-visible state.
function shouldRefreshForEvent(eventType: string): boolean {
  if (isRefreshNoiseEventType(eventType)) return false;
  if (LOCAL_AGENT_STREAM_EVENTS.has(eventType)) return false;
  return (
    REFRESH_EVENT_TYPES.has(eventType)
    || REFRESH_EVENT_PREFIXES.some((prefix) => eventType.startsWith(prefix))
  );
}

function connectionStatusView({
  activeProjectId,
  error,
  liveState,
  snapshot,
  snapshotRequired,
}: {
  activeProjectId: string;
  error: string | null;
  liveState: LiveState;
  snapshot: Snapshot | null;
  snapshotRequired: boolean;
}): { className: string; label: string; title: string } {
  if (!activeProjectId) {
    return { className: "status-idle", label: "no project", title: "No active workspace project." };
  }
  if (error) {
    return { className: "status-degraded", label: "degraded", title: error };
  }
  if (!snapshot) {
    if (!snapshotRequired) {
      if (liveState === "live") {
        return { className: "status-live", label: "synced", title: "Project slice and event stream are connected." };
      }
      if (liveState === "reconnecting") {
        return { className: "status-reconnecting", label: "stream reconnecting", title: "Project slice is loaded; event stream is reconnecting." };
      }
      if (liveState === "degraded") {
        return { className: "status-degraded", label: "stream degraded", title: "Project slice is loaded; event stream needs recovery." };
      }
      return { className: "status-loading", label: "stream pending", title: "Project slice is rendered; waiting for event stream connection." };
    }
    return { className: "status-loading", label: "snapshot pending", title: "Project shell is rendered; waiting for the snapshot projection." };
  }
  if ((snapshot.runtime as { runtime_state?: string }).runtime_state === "archived") {
    return { className: "status-idle", label: "archived", title: "Archived project: data is a historical record, no live runtime." };
  }
  if (liveState === "live") {
    return { className: "status-live", label: "synced", title: "Snapshot and project event stream are connected." };
  }
  if (liveState === "reconnecting") {
    return { className: "status-reconnecting", label: "stream reconnecting", title: "Snapshot is loaded; event stream is reconnecting." };
  }
  if (liveState === "degraded") {
    return { className: "status-degraded", label: "stream degraded", title: "Snapshot is loaded; event stream needs recovery." };
  }
  return { className: "status-loading", label: "stream pending", title: "Snapshot is loaded; waiting for event stream connection." };
}

function emptyNewChannelDraft(): NewChannelDraft {
  return {
    name: "# ",
    channelId: "",
  };
}

function emptyProjectWizardDraft(
  onboarding?: OnboardingStatus | null,
): ProjectAdmissionDraft {
  return {
    root: "",
    name: "",
    description: "",
    stack: "",
    backend: onboarding?.primary_backend || onboarding?.backend || "codex",
    mixedEnabled: Boolean(
      onboarding?.mixed_available && onboarding?.mixed_enabled,
    ),
  };
}

function projectNameFromPath(path: string): string {
  const normalized = path.trim().replace(/[\\/]+$/, "");
  const parts = normalized.split(/[\\/]/);
  return parts[parts.length - 1] || "zaofu-project";
}

function storedThemeMode(): ThemeMode {
  if (typeof window === "undefined") return "system";
  const value = window.localStorage.getItem("zf.themeMode");
  return value === "dark" || value === "light" || value === "system" ? value : "system";
}

function skillRefOptions(summary: SkillsSummary | null | undefined): string[] {
  const values = new Set<string>();
  (summary?.pool ?? []).forEach((skill) => {
    if (skill.path) values.add(skill.path);
    if (skill.name) values.add(`skills/${skill.name}/SKILL.md`);
  });
  return Array.from(values).sort();
}

function normalizeWebActionToken(value: string): string {
  let base = value.trim();
  if (
    base.length >= 2
    && ((base.startsWith('"') && base.endsWith('"'))
      || (base.startsWith("'") && base.endsWith("'"))
      || (base.startsWith("`") && base.endsWith("`")))
  ) {
    base = base.slice(1, -1).trim();
  }
  // Header values must be latin1 (ByteString). A token pasted from the masked
  // password field (• = U+2022) or carrying stray unicode would otherwise
  // crash fetch ("character ... greater than 255"). Keep only printable ASCII.
  let out = "";
  for (const ch of base) {
    const code = ch.charCodeAt(0);
    if (code >= 0x21 && code <= 0x7e) out += ch;
  }
  return out;
}

function storedWebActionToken(): string {
  if (typeof window === "undefined") return "";
  return normalizeWebActionToken(window.localStorage.getItem("zf.webActionToken") ?? "");
}

function isNoiseTask(task: Task): boolean {
  const source = String(task.source ?? "").toLowerCase();
  if (["playwright", "e2e", "test", "tests"].includes(source)) return true;
  const title = `${task.title} ${task.id}`.toLowerCase();
  return /^(drag move|locked drag|workbench drilldown|operator task rebind|kanban agent lifecycle probe)\b/.test(title)
    || title.includes("playwright");
}

function projectCanOpenBoard(project: WorkspaceProject | null | undefined): boolean {
  if (!project) return false;
  if (project.can_open_board === true) return true;
  if (project.lifecycle) return project.lifecycle.can_open_board === true;
  return true;
}

function projectLifecycleReason(project: WorkspaceProject | null | undefined): string {
  if (!project) return "No Project selected.";
  return project.lifecycle?.reason
    || (project.lifecycle?.has_config === false ? "zf.yaml is missing." : "")
    || (project.lifecycle?.initialized === false ? "Project runtime state is not initialized." : "")
    || "Project is not ready to open.";
}

function headlessActionProposal(payload: Record<string, unknown>): HeadlessActionProposal | null {
  // Web panel replies carry `action_proposal`; operator.action.proposed
  // events (e.g. the Feishu-surface loop) carry the same object as `proposal`.
  const proposal = recordValue(payload.action_proposal) ?? recordValue(payload.proposal);
  if (!proposal) return null;
  const action = textValue(proposal.action).trim();
  const nestedPayload = recordValue(proposal.payload);
  if (!action || !nestedPayload) return null;
  return {
    action,
    requestedAction: textValue(proposal.requested_action || proposal.action).trim(),
    payload: nestedPayload,
    reason: textValue(proposal.reason).trim(),
    confidence: textValue(proposal.confidence).trim(),
    valid: proposal.valid !== false,
    validationError: textValue(proposal.validation_error).trim(),
  };
}

function headlessDeltaBody(payload: Record<string, unknown>): string {
  const type = textValue(payload.message_type || payload.type).trim();
  if (type === "tool_use") {
    const tool = textValue(payload.tool).trim() || "tool";
    const input = recordValue(payload.input);
    return input ? `${tool}\n${stringify(input)}` : tool;
  }
  if (type === "tool_result") {
    return textValue(payload.output || payload.content).trim();
  }
  return textValue(payload.content || payload.status || payload.tool).trim();
}

function headlessDeltaTrace(payload: Record<string, unknown>): string {
  const type = textValue(payload.message_type || payload.type).trim();
  if (type === "tool_use") {
    const tool = textValue(payload.tool).trim() || "tool";
    return `tool ${tool}`;
  }
  if (type === "tool_result") return "tool result";
  if (type === "thinking") return "thinking";
  if (type === "status") return textValue(payload.status || payload.content).trim();
  return "";
}

function headlessStageForDelta(payload: Record<string, unknown>): {
  stage: HeadlessThreadItem["stage"];
  label: string;
} {
  const type = textValue(payload.message_type || payload.type).trim();
  if (type === "text") return { stage: "typing", label: "Typing" };
  if (type === "thinking") return { stage: "thinking", label: "Thinking" };
  if (type === "tool_use") {
    const tool = textValue(payload.tool).trim().toLowerCase();
    const labelByTool: Record<string, string> = {
      bash: "Running command",
      exec: "Running command",
      exec_command: "Running command",
      read: "Reading files",
      glob: "Reading files",
      grep: "Searching code",
      write: "Making edits",
      edit: "Making edits",
      multi_edit: "Making edits",
      multiedit: "Making edits",
      patch_apply: "Making edits",
      web_search: "Searching web",
      websearch: "Searching web",
    };
    return { stage: "tool", label: labelByTool[tool] ?? (tool ? `Running ${tool}` : "Working") };
  }
  if (type === "tool_result") return { stage: "thinking", label: "Thinking" };
  if (type === "status") {
    const content = textValue(payload.status || payload.content).trim().toLowerCase();
    if (content === "running") return { stage: "thinking", label: "Thinking" };
    return { stage: "starting", label: "Starting" };
  }
  return { stage: "thinking", label: "Thinking" };
}

function proposalButtonLabel(proposal: HeadlessActionProposal): string {
  if (proposal.action === "create-task") return "Create Task";
  return "Run action";
}

function canonicalChatBackend(backend: OperatorBackend): OperatorBackend {
  if (backend === "claude-code") return "claude-headless";
  if (backend === "codex") return "codex-headless";
  return backend;
}

function runtimeActionState(
  snapshot: Snapshot | null,
  tokenPresent: boolean,
  fallbackWebSession: RuntimeSummary["web_session"] | null,
): RuntimeActionState {
  const webSession = snapshot?.runtime.web_session ?? fallbackWebSession;
  const mutationEnabled = Boolean(snapshot?.runtime.actions?.mutation_enabled)
    || Boolean(webSession && webSession.mode !== "read_only")
    || tokenPresent;
  const sessionActionReady = Boolean(webSession?.actions_enabled);
  const tokenFallbackAvailable = Boolean(tokenPresent)
    || webSession?.mode === "token_required"
    || Boolean(webSession?.token_fallback_enabled);
  const passcodeRequired = webSession?.mode === "remote_passcode" && !sessionActionReady;
  const showTokenRow = mutationEnabled && !sessionActionReady && tokenFallbackAvailable && !tokenPresent;
  const tokenRequired = showTokenRow && !tokenPresent;
  const actionReady = sessionActionReady || (mutationEnabled && tokenPresent);
  const actionState = actionReady
    ? "active"
    : mutationEnabled
      ? (passcodeRequired ? "passcode needed" : tokenRequired ? "token needed" : "locked")
      : "read only";
  return {
    actionReady,
    actionState,
    mutationEnabled,
    passcodeRequired,
    sessionActionReady,
    showTokenRow,
    tokenRequired,
  };
}

export function App() {
  useSettingsPreferences();
  const developerAccess = useDeveloperAccess();
  useEffect(() => {
    void refreshDeveloperAccess();
    const visible = () => { if (!document.hidden) void refreshDeveloperAccess(); };
    document.addEventListener("visibilitychange", visible);
    return () => document.removeEventListener("visibilitychange", visible);
  }, []);
  const initial = useMemo(() => readInitialQuery(), []);
  const initialSelfIssueOAuthCallback = useMemo(
    () => selfIssueOAuthCallback(window.location.search),
    [],
  );
  const [workspaceProjects, setWorkspaceProjects] = useState<WorkspaceProject[]>([]);
  // The project `zf web` was started with. It is re-injected into every
  // /api/workspace/projects response, so deleting it from the registry is a
  // silent no-op — disable its delete affordance instead of faking success.
  const [serverDefaultProjectId, setServerDefaultProjectId] = useState("");
  // chat-e2e F1: remember the operator's project choice per origin (port =
  // server), so a reload keeps their switch without consulting the global
  // workspace active pointer that other servers mutate.
  const [activeProjectId, setActiveProjectId] = useState(
    initial.project || window.localStorage.getItem("zf.activeProjectId") || "",
  );
  const projectRequestScope = useProjectRequestScope(activeProjectId);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [deliveryFeaturesPage, setDeliveryFeaturesPage] = useState<DeliveryFeaturesPage | null>(null);
  const [events, setEvents] = useState<RecentEvent[]>([]);
  const [eventsPage, setEventsPage] = useState<EventsPage | null>(null);
  const [channelsPage, setChannelsPage] = useState<ChannelsPage | null>(null);
  // Triage proposal-only queue: pending kanban-agent proposals are ledger
  // truth (see OrchestratorPanel), not live-session state. Sourcing the
  // Autopilot queue only from the bounded recent-events slice dropped
  // still-pending proposals once they aged past the event window, silently
  // removing the operator's Accept entry point. Fetch the durable projection.
  const [kanbanPendingProposals, setKanbanPendingProposals] = useState<PendingKanbanProposal[]>([]);
  const [inboxPendingCount, setInboxPendingCount] = useState(0);
  const [selectedChannelId, setSelectedChannelId] = useState(initial.channel);
  const [channelDetail, setChannelDetail] = useState<ChannelDetail | null>(null);
  const [channelLoadError, setChannelLoadError] = useState<string | null>(null);
  const [channelDiagnosticsDetail, setChannelDiagnosticsDetail] = useState<ChannelDetail | null>(null);
  const [channelDiagnosticsLoading, setChannelDiagnosticsLoading] = useState(false);
  const [channelDiagnosticsError, setChannelDiagnosticsError] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(initial.task);
  const [taskDetail, setTaskDetail] = useState<TaskDetailModel | null>(null);
  const [taskDiff, setTaskDiff] = useState<TaskDiff | null>(null);
  const [taskTimeline, setTaskTimeline] = useState<TaskTimeline | null>(null);
  const [taskTimelineLoading, setTaskTimelineLoading] = useState(false);
  const [taskTimelineError, setTaskTimelineError] = useState<string | null>(null);
  const [taskLoadError, setTaskLoadError] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("Summary");
  const [page, setPage] = useState<PageId>(initial.page);
  const [issueTriageTab, setIssueTriageTab] = useState<TriageTab>("github");
  const [viewMode, setViewMode] = useState<ViewMode>(initial.view);
  const [statusFilter, setStatusFilter] = useState(initial.status);
  const [assigneeFilter, setAssigneeFilter] = useState("all");
  const [skillFilter, setSkillFilter] = useState("all");
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [quickFilter, setQuickFilter] = useState("all");
  const [textFilter, setTextFilter] = useState("");
  const [eventFilter, setEventFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [projectionDetail, setProjectionDetail] = useState<Record<string, unknown> | null>(null);
  const projectionNavigationRef = useRef("");
  const [integrationQueue, setIntegrationQueue] = useState<IntegrationQueueProjection | null>(null);
  const [taskPipeline, setTaskPipeline] = useState<TaskPipelineProjection | null>(null);
  const [repairActions, setRepairActions] = useState<RepairActionProjection | null>(null);
  const [actionResult, setActionResult] = useState<ActionResponse | null>(null);
  const [selfIssueOAuthCallbackPending, setSelfIssueOAuthCallbackPending] = useState(
    Boolean(initialSelfIssueOAuthCallback),
  );
  const selfIssueOAuthCallbackStarted = useRef(false);
  const [webActionTokenPresent, setWebActionTokenPresent] = useState(() =>
    Boolean(storedWebActionToken()),
  );
  const [webSessionStatus, setWebSessionStatus] = useState<NonNullable<RuntimeSummary["web_session"]> | null>(null);
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => storedThemeMode());
  const [agentPanelMode, setAgentPanelMode] = useState<AgentPanelMode>(
    initial.openAgent ? "docked" : "collapsed",
  );
  const [agentPanelHasOpened, setAgentPanelHasOpened] = useState(initial.openAgent);
  const [commandOpen, setCommandOpen] = useState(false);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [newChannelOpen, setNewChannelOpen] = useState(false);
  const [newChannelDraft, setNewChannelDraft] = useState<NewChannelDraft>(() => emptyNewChannelDraft());
  const [addAgentOpen, setAddAgentOpen] = useState(false);
  const [addAgentDraft, setAddAgentDraft] = useState<AddAgentDraft>(() => emptyAddAgentDraft());
  const [onboardingStatus, setOnboardingStatus] = useState<OnboardingStatus | null>(null);
  const [projectWizardOpen, setProjectWizardOpen] = useState(false);
  const [projectWizardDraft, setProjectWizardDraft] = useState<ProjectAdmissionDraft>(() => emptyProjectWizardDraft());
  const [projectWizardInspection, setProjectWizardInspection] = useState<WorkspaceProjectPathInspection | null>(null);
  const [projectWizardBusy, setProjectWizardBusy] = useState(false);
  const [projectWizardError, setProjectWizardError] = useState("");
  const [showWelcome, setShowWelcome] = useState<boolean | null>(null);
  useEffect(() => {
    if (!initialSelfIssueOAuthCallback || selfIssueOAuthCallbackStarted.current) return;
    selfIssueOAuthCallbackStarted.current = true;
    const callbackIsPopup = window.sessionStorage.getItem("zf.selfIssueOAuthPopup") === "1"
      || Boolean(window.opener && window.opener !== window);
    void submitAction("self-issue-oauth-callback", {
      ...initialSelfIssueOAuthCallback,
      session_id: selfIssueOAuthSession(),
    }).then((result) => {
      setActionResult(result);
      const completion = {
        type: "zf:self-issue-oauth-complete",
        completed_at: new Date().toISOString(),
        result,
      };
      window.localStorage.setItem("zf.selfIssueOAuthCompleted", JSON.stringify(completion));
      if (window.opener && window.opener !== window) {
        window.opener.postMessage(completion, window.location.origin);
      }
    }).catch((error: unknown) => {
      const failure = {
        ok: false,
        status: "self_issue_oauth_callback_failed",
        action: "self-issue-oauth-callback",
        requested_action: "self-issue-oauth-callback",
        reason: error instanceof Error ? error.message : String(error),
      };
      setActionResult(failure);
      const completion = {
        type: "zf:self-issue-oauth-complete",
        completed_at: new Date().toISOString(),
        result: failure,
      };
      window.localStorage.setItem("zf.selfIssueOAuthCompleted", JSON.stringify(completion));
      if (window.opener && window.opener !== window) {
        window.opener.postMessage(completion, window.location.origin);
      }
    }).finally(() => {
      const suffix = withoutSelfIssueOAuthCallback(window.location.search);
      window.history.replaceState(
        {},
        "",
        `${window.location.pathname}${suffix ? `?${suffix}` : ""}${window.location.hash}`,
      );
      setSelfIssueOAuthCallbackPending(false);
      window.sessionStorage.removeItem("zf.selfIssueOAuthPopup");
      if (callbackIsPopup) {
        window.setTimeout(() => window.close(), 250);
      } else {
        setAgentPanelHasOpened(true);
        setAgentPanelMode("docked");
      }
    });
  }, [initialSelfIssueOAuthCallback]);
  useEffect(() => {
    let cancelled = false;
    getOnboarding()
      .then((o) => {
        if (!cancelled) {
          setOnboardingStatus(o);
          setShowWelcome(o.show_welcome);
        }
      })
      .catch(() => { if (!cancelled) setShowWelcome(false); });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    let cancelled = false;
    // Revoke the previous capability while a newly supplied token is being
    // checked. This prevents a developer Triage view from flashing when the
    // browser switches back to a reporter token.
    setWebSessionStatus(null);
    getWebSession()
      .then((session) => {
        if (!cancelled) setWebSessionStatus(session);
      })
      .catch(() => {
        if (!cancelled) {
          setWebSessionStatus({
            mode: "read_only",
            unlocked: false,
            actions_enabled: false,
            can_view_issue_triage: false,
          });
        }
      });
    return () => { cancelled = true; };
  }, [webActionTokenPresent]);
  useEffect(() => {
    const update = () => { void getWebSession().then(setWebSessionStatus).catch(() => setWebSessionStatus(null)); };
    window.addEventListener("zf:developer-access-changed", update);
    return () => window.removeEventListener("zf:developer-access-changed", update);
  }, []);
  useEffect(() => {
    if (page !== "issue-triage" || webSessionStatus === null || developerAccess.loading) return;
    if (developerAccess.mode === "developer" && developerAccess.verified && webSessionStatus.can_view_issue_triage === true) return;
    // A query-string route is not an authorization boundary. Redirect a
    // reporter who manually enters ?page=issue-triage back to the board after
    // the server has denied the developer capability.
    setPage("board");
  }, [page, webSessionStatus, developerAccess.mode, developerAccess.verified, developerAccess.loading]);
  const [orchestratorFocusSignal, setOrchestratorFocusSignal] = useState(0);
  const [liveState, setLiveState] = useState<LiveState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const eventBusRef = useRef<ProjectEventBus | null>(null);
  const refreshRef = useRef<(() => void | Promise<void>) | null>(null);
  const sliceRefreshTimersRef = useRef<Map<string, number>>(new Map());
  const liveRefreshRef = useRef<((event: RecentEvent, reason: "event" | "gap" | "error") => void) | null>(null);
  const lastSeqRef = useRef(0);
  const selectedChannelIdRef = useRef(selectedChannelId);
  const channelDetailRequestGateRef = useRef(new LatestRequestGate());
  const channelHistoryRequestGateRef = useRef(new LatestRequestGate());
  const channelDiagnosticsRequestGateRef = useRef(new LatestRequestGate());
  const recoverStreamGap = useProjectStreamGapRecovery({
    activeProjectId, page, selectedChannelId, lastSeqRef, setEvents, setSnapshot, setDeliveryFeaturesPage,
    setChannelsPage, setChannelLoadError, setSelectedChannelId,
    setChannelDetail: (fresh) => setChannelDetail((current) => (
      mergeChannelConversationRefresh(current, fresh)
    )),
    channelDetailRequestGate: channelDetailRequestGateRef.current,
    setKanbanPendingProposals, setError,
  });

  const selectedTask = useMemo(() => {
    if (!selectedTaskId) return null;
    if (snapshot) {
      const fromBoard = allBoardTasks(snapshot).find((task) => task.id === selectedTaskId);
      if (fromBoard) return fromBoard;
    }
    // Fall back to the independently-fetched task detail so the task page renders
    // its header without waiting for the (shell) light snapshot to load/resolve.
    if (taskDetail?.task && taskDetail.task.id === selectedTaskId) return taskDetail.task;
    return null;
  }, [selectedTaskId, snapshot, taskDetail]);
  const actionGate = useMemo(
    () => runtimeActionState(snapshot, webActionTokenPresent, webSessionStatus),
    [snapshot, webActionTokenPresent, webSessionStatus],
  );
  const activeProject = useMemo(
    () => workspaceProjects.find((project) => project.project_id === activeProjectId) ?? null,
    [activeProjectId, workspaceProjects],
  );
  const activeProjectReady = projectCanOpenBoard(activeProject);

  useEffect(() => {
    selectedChannelIdRef.current = selectedChannelId;
  }, [selectedChannelId]);

  useEffect(() => {
    channelHistoryRequestGateRef.current.invalidate();
    channelDiagnosticsRequestGateRef.current.invalidate();
    setChannelDiagnosticsDetail(null);
    setChannelDiagnosticsLoading(false);
    setChannelDiagnosticsError(null);
  }, [activeProjectId, selectedChannelId]);

  useEffect(() => {
    if (activeProjectId) {
      window.localStorage.setItem("zf.activeProjectId", activeProjectId);
    }
  }, [activeProjectId]);

  const loadWorkspaceProjects = useCallback(async () => {
    const page = await getWorkspaceProjects();
    setWorkspaceProjects(page.items ?? page.projects ?? []);
    setServerDefaultProjectId(page.server_default_project_id ?? "");
    if (!activeProjectId && page.active_project_id) {
      projectRequestScope.activate(page.active_project_id);
      setActiveProjectId(page.active_project_id);
    }
    return page;
  }, [activeProjectId]);

  const loadSnapshot = useCallback(async (kindOverride?: "light" | "full") => {
    const snapshotKind = kindOverride ?? snapshotLoadKindForPage(page);
    if (snapshotKind === "none") return null;
    const requestedProjectId = activeProjectId || "";
    const ticket = projectRequestScope.capture(requestedProjectId);
    const next = snapshotKind === "full"
      ? await getSnapshot(requestedProjectId || undefined)
      : await getSnapshotLight(requestedProjectId || undefined);
    if (
      requestedProjectId
      && next.project?.project_id
      && next.project.project_id !== requestedProjectId
    ) {
      return next;
    }
    if (!projectRequestScope.isCurrent(ticket)) return next;
    lastSeqRef.current = Math.max(lastSeqRef.current, next.seq);
    setSnapshot(next);
    setError(null);
    return next;
  }, [activeProjectId, page]);

  const loadDeliveryFeatures = useCallback(async () => {
    const requestedProjectId = activeProjectId || "";
    const ticket = projectRequestScope.capture(requestedProjectId);
    const next = await getDeliveryFeatures(requestedProjectId || undefined);
    if (!projectRequestScope.isCurrent(ticket)) return next;
    setDeliveryFeaturesPage(next);
    return next;
  }, [activeProjectId]);

  const loadChannels = useCallback(async () => {
    const requestedProjectId = activeProjectId || "";
    const ticket = projectRequestScope.capture(requestedProjectId);
    const next = await getChannels(requestedProjectId || undefined);
    if (!projectRequestScope.isCurrent(ticket)) return next;
    setChannelsPage(next);
    setChannelLoadError(null);
    setSelectedChannelId((current) => (
      current && next.channels.some((item) => channelIdOf(item) === current)
        ? current
        : channelIdOf(next.channels[0]) || "ch-zaofu"
    ));
    if (projectionNeedsFresh(next)) {
      const timers = sliceRefreshTimersRef.current;
      const key = "channels-fresh";
      const existing = timers.get(key);
      if (existing !== undefined) window.clearTimeout(existing);
      timers.set(key, window.setTimeout(() => {
        timers.delete(key);
        void getChannels(requestedProjectId || undefined, true).then((fresh) => {
          if (!projectRequestScope.isCurrent(ticket)) return;
          setChannelsPage(fresh);
          setChannelLoadError(null);
          setSelectedChannelId((current) => (
            current && fresh.channels.some((item) => channelIdOf(item) === current)
              ? current
              : channelIdOf(fresh.channels[0]) || "ch-zaofu"
          ));
        }).catch(() => undefined);
      }, 2500));
    }
    return next;
  }, [activeProjectId]);

  const loadEarlierChannelMessages = useCallback(async () => {
    const channelId = selectedChannelId || "ch-zaofu";
    const cursor = channelDetail?.next_before || channelDetail?.page?.next_before || "";
    if (!cursor || !channelDetail?.has_more) return;
    const projectId = activeProjectId || undefined;
    const ticket = channelHistoryRequestGateRef.current.issue();
    const earlier = await getChannelConversation(channelId, projectId, {
      before: cursor,
      limit: 50,
      requireFresh: true,
    });
    if (!channelHistoryRequestGateRef.current.isCurrent(ticket)) return;
    if (selectedChannelIdRef.current !== channelId) return;
    setChannelDetail((current) => (
      current ? mergeChannelConversationPages(current, earlier) : earlier
    ));
  }, [activeProjectId, channelDetail, selectedChannelId]);

  const loadChannelDiagnostics = useCallback(async () => {
    const channelId = selectedChannelId || "ch-zaofu";
    const conversationSeq = Number(channelDetail?.seq || 0);
    if (channelDiagnosticsLoading) return;
    if (
      channelDiagnosticsDetail?.channel_id === channelId
      && Number(channelDiagnosticsDetail.seq || 0) >= conversationSeq
    ) return;
    const projectId = activeProjectId || undefined;
    const ticket = channelDiagnosticsRequestGateRef.current.issue();
    setChannelDiagnosticsLoading(true);
    setChannelDiagnosticsError(null);
    try {
      const detail = await getChannelDetail(channelId, projectId);
      if (!channelDiagnosticsRequestGateRef.current.isCurrent(ticket)) return;
      if (selectedChannelIdRef.current !== channelId) return;
      setChannelDiagnosticsDetail(detail);
    } catch (err) {
      if (!channelDiagnosticsRequestGateRef.current.isCurrent(ticket)) return;
      setChannelDiagnosticsError(err instanceof Error ? err.message : String(err));
    } finally {
      if (channelDiagnosticsRequestGateRef.current.isCurrent(ticket)) {
        setChannelDiagnosticsLoading(false);
      }
    }
  }, [activeProjectId, channelDetail?.seq, channelDiagnosticsDetail, channelDiagnosticsLoading, selectedChannelId]);

  const loadKanbanProposals = useCallback(async () => {
    const requestedProjectId = activeProjectId || "";
    const ticket = projectRequestScope.capture(requestedProjectId);
    try {
      const page = await getKanbanPendingProposals(requestedProjectId || undefined);
      if (!projectRequestScope.isCurrent(ticket)) return;
      setKanbanPendingProposals(page.items ?? []);
    } catch {
      if (!projectRequestScope.isCurrent(ticket)) return;
      setKanbanPendingProposals([]);
    }
  }, [activeProjectId]);

  const refresh = useCallback(async () => {
    const ticket = projectRequestScope.capture(activeProjectId);
    if (!activeProjectId || (activeProject && !activeProjectReady)) {
      eventBusRef.current?.close();
      setSnapshot(null);
      setDeliveryFeaturesPage(null);
      setIntegrationQueue(null);
      setTaskPipeline(null);
      setRepairActions(null);
      setChannelsPage(null);
      setError(null);
      setLiveState("connecting");
      return;
    }
    try {
      const owns = (resource: BootstrapResource) => (
        pageOwnsBootstrapResource(page, resource)
      );
      const requests: Array<Promise<unknown>> = [
        getProjectHealth(activeProjectId || undefined)
          .then((health) => {
            if (projectRequestScope.isCurrent(ticket)) setProjectHealth(health);
          })
          .catch(() => {
            if (projectRequestScope.isCurrent(ticket)) setProjectHealth(null);
          }),
      ];
      if (owns("channels.summary")) requests.push(loadChannels());
      if (owns("events.recent")) {
        requests.push(getRecentEventsPage(60, activeProjectId || undefined, true).then((recent) => {
          if (projectRequestScope.isCurrent(ticket)) setEvents(recent.items.slice().reverse());
        }));
      }
      if (pageLoadsSnapshot(page)) requests.push(loadSnapshot());
      if (pageLoadsDeliveryFeatures(page)) requests.push(loadDeliveryFeatures());
      await Promise.all(requests);
    } catch (err) {
      if (!projectRequestScope.isCurrent(ticket)) return;
      setError(err instanceof Error ? err.message : String(err));
      setLiveState("degraded");
    }
  }, [activeProject, activeProjectId, activeProjectReady, loadChannels, loadDeliveryFeatures, loadSnapshot, page]);

  // Triage proposal-only queue: load the durable pending-proposals projection
  // whenever the operator is on the Triage page. Kept independent of the
  // snapshot-readiness gate above so aged-out-but-pending proposals always
  // resurface their Accept entry point.
  useEffect(() => {
    const triageQueueVisible = page === "triage"
      || (page === "issue-triage" && issueTriageTab === "runtime");
    if (!triageQueueVisible || !activeProjectId) {
      setKanbanPendingProposals([]);
      return;
    }
    void loadKanbanProposals();
  }, [page, issueTriageTab, activeProjectId, loadKanbanProposals]);

  // Issue Triage intentionally avoids the expensive Kanban snapshot on its
  // Issues tab. Load the runtime snapshot only when the operator explicitly
  // opens Runtime interventions, where the legacy intervention queue needs it.
  useEffect(() => {
    if (page !== "issue-triage" || issueTriageTab !== "runtime" || !activeProjectId || !activeProjectReady) return;
    void loadSnapshot();
  }, [activeProjectId, activeProjectReady, issueTriageTab, loadSnapshot, page]);

  // The Issues tab intentionally skips the expensive Kanban snapshot, but the
  // embedded Kanban Agent still needs the snapshot's action allow-list before
  // it can execute a pending workflow proposal. Load the light snapshot when
  // the operator opens the Agent so a visible proposal is never silently
  // dropped by the action gate while `snapshot` is still null.
  useEffect(() => {
    // Issue/Triage remains usable while the runtime is stopped. The light
    // snapshot is also the source of the action allow-list; gating this read
    // on board readiness leaves visible proposals without a submit handler.
    if (
      agentPanelMode === "collapsed"
      || (snapshot?.project?.project_id && snapshot.project.project_id === activeProjectId)
      || !activeProjectId
    ) return;
    void loadSnapshot("light");
    // Project bootstrap can invalidate the first read while the workspace
    // registry settles. Retry a few times so the Agent cannot remain locked
    // with a visible proposal after that benign race.
    const retries = [500, 1500, 3000].map((delay) => window.setTimeout(() => {
      void loadSnapshot("light");
    }, delay));
    return () => retries.forEach((timer) => window.clearTimeout(timer));
  }, [activeProjectId, activeProjectReady, agentPanelMode, loadSnapshot, snapshot]);

  useEffect(() => {
    refreshRef.current = refresh;
  }, [refresh]);

  useEffect(() => {
    liveRefreshRef.current = (event, reason) => {
      // Trailing debounce: coalesce event bursts into one slice reload.
      // A single real event still refreshes within ~1.5s.
      const scheduleSlice = (key: string, fn: () => void) => {
        const timers = sliceRefreshTimersRef.current;
        const existing = timers.get(key);
        if (existing !== undefined) window.clearTimeout(existing);
        timers.set(key, window.setTimeout(() => {
          timers.delete(key);
          fn();
        }, 1500));
      };
      if (reason !== "event") {
        void refreshRef.current?.();
        return;
      }
      const eventType = event.type || "";
      const payload = asRecord(event.payload);
      if (
        (page === "triage" || (page === "issue-triage" && issueTriageTab === "runtime"))
        && (eventType === "kanban.agent.action.proposed"
          || eventType === "kanban.agent.proposal.resolved"
          || eventType === "operator.action.proposed"
          || eventType === "operator.action.resolved"
          || eventType === "task.created")
      ) {
        scheduleSlice("kanban-proposals", () => void loadKanbanProposals());
      }
      const channelRefresh = channelEventRefreshPlan(eventType);
      if (channelRefresh.conversation) {
        if (page === "channels") {
          if (channelRefresh.summary) {
            scheduleSlice("channels", () => void loadChannels());
          }
          const channelId = textValue(payload.channel_id) || selectedChannelIdRef.current;
          if (!channelId || channelId === selectedChannelIdRef.current) {
            scheduleSlice(`channel-conversation:${channelId || "selected"}`, () => {
              const ticket = projectRequestScope.capture(activeProjectId);
              const detailTicket = channelDetailRequestGateRef.current.issue();
              void getChannelConversation(
                selectedChannelIdRef.current || "ch-zaofu",
                activeProjectId || undefined,
                { requireFresh: true },
              )
                .then((detail) => {
                  if (!projectRequestScope.isCurrent(ticket)) return;
                  if (!channelDetailRequestGateRef.current.isCurrent(detailTicket)) return;
                  if (selectedChannelIdRef.current === channelId || !channelId) {
                    setChannelDetail((current) => mergeChannelConversationRefresh(current, detail));
                  }
                })
                .catch(() => undefined);
            });
          }
        }
        return;
      }
      if (
        eventType.startsWith("task.")
        || eventType.startsWith("feature.")
        || eventType.startsWith("dev.")
        || eventType.startsWith("review.")
        || eventType.startsWith("test.")
        || eventType.startsWith("judge.")
      ) {
        if (BOARD_REFRESH_PAGES.has(page)
          || (page === "issue-triage" && issueTriageTab === "runtime")) {
          scheduleSlice("snapshot", () => void loadSnapshot());
        }
        if (MEASURE_REFRESH_PAGES.has(page)) scheduleSlice("delivery", () => void loadDeliveryFeatures());
        return;
      }
      if (
        eventType.startsWith("candidate.")
        || eventType.startsWith("fanout.")
        || eventType.startsWith("workflow.invoke.")
        || eventType.startsWith("run.")
        || eventType.startsWith("ship.")
      ) {
        if (MEASURE_REFRESH_PAGES.has(page)) scheduleSlice("delivery", () => void loadDeliveryFeatures());
        if (isObservabilityPage(page)) scheduleSlice("snapshot", () => void loadSnapshot());
        return;
      }
      if (
        eventType.startsWith("worker.")
        || eventType.startsWith("agent.session.")
        || eventType.startsWith("runtime.action.")
        || eventType.startsWith("workdir.")
      ) {
        if (page === "runtime") scheduleSlice("snapshot", () => void loadSnapshot());
        return;
      }
      if (eventType.startsWith("operator.") && page === "inbox") {
        scheduleSlice("snapshot", () => void loadSnapshot());
      }
    };
  }, [activeProjectId, issueTriageTab, loadChannels, loadDeliveryFeatures, loadKanbanProposals, loadSnapshot, page]);

  // Unified header source (doc116 §5/§11.1): cheap canonical health, never
  // the snapshot bundle — pages that skip the snapshot still get a truthful
  // pill and counts.
  const [projectHealth, setProjectHealth] = useState<import("../api/client").ProjectHealthSummary | null>(null);
  useEffect(() => {
    if (!activeProjectId) {
      setProjectHealth(null);
      return undefined;
    }
    let cancelled = false;
    const pull = async () => {
      try {
        const health = await getProjectHealth(activeProjectId || undefined);
        if (!cancelled) setProjectHealth(health);
      } catch {
        if (!cancelled) setProjectHealth(null);
      }
    };
    void pull();
    const timer = window.setInterval(() => void pull(), 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeProjectId]);

  // Operator Inbox pending count → nav badge. Polled; read-only projection.
  useEffect(() => {
    if (!activeProjectId) {
      setInboxPendingCount(0);
      return undefined;
    }
    if (!pagePollsOperatorInbox(page)) {
      return undefined;
    }
    let cancelled = false;
    const pull = async () => {
      try {
        const inbox = await getOperatorInbox(activeProjectId || undefined);
        if (!cancelled) {
          setInboxPendingCount((inbox?.pending ?? []).filter((item) => (
            item.actionability === "human_required"
          )).length);
        }
      } catch {
        /* read-only; CLI remains the fallback surface */
      }
    };
    void pull();
    const timer = window.setInterval(() => void pull(), 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeProjectId, page]);

  useEffect(() => {
    const current = new URLSearchParams(window.location.search);
    const params = new URLSearchParams();
    for (const [key, value] of current.entries()) {
      if (
        (page === "observability" && key.startsWith("obs_"))
        || (page === "behavior-loop" && BEHAVIOR_LOOP_QUERY_KEYS.has(key))
        || PAGE_QUERY_KEYS[page]?.has(key)
      ) {
        params.set(key, value);
      }
    }
    if (activeProjectId) params.set("project", activeProjectId);
    params.set("page", page);
    if (page === "board") {
      params.set("view", viewMode);
      if (statusFilter !== "all") params.set("status", statusFilter);
    }
    if (page === "channels") params.set("channel", selectedChannelId);
    if (selectedTaskId) params.set("task", selectedTaskId);
    window.history.replaceState(null, "", `?${params.toString()}`);
  }, [activeProjectId, page, selectedChannelId, selectedTaskId, statusFilter, viewMode]);

  useEffect(() => {
    let cancelled = false;
    if (!pageLoadsDeliveryFeatures(page)) {
      setDeliveryFeaturesPage(null);
    }

    async function bootstrap() {
      try {
        const snapshotKind = snapshotLoadKindForPage(page);
        const owns = (resource: BootstrapResource) => (
          pageOwnsBootstrapResource(page, resource)
        );
        const projectsRequest = getWorkspaceProjects().catch(() => null);
        const bootstrapBeforeWorkspace = canBootstrapScopedPageBeforeWorkspace(
          page,
          initial.project === activeProjectId ? activeProjectId : "",
        );
        const projectsPage = bootstrapBeforeWorkspace ? null : await projectsRequest;
        if (bootstrapBeforeWorkspace) {
          void projectsRequest.then((workspacePage) => {
            if (cancelled || !workspacePage) return;
            setWorkspaceProjects(workspacePage.items ?? workspacePage.projects ?? []);
            setServerDefaultProjectId(workspacePage.server_default_project_id ?? "");
          });
        }
        const projectItems = projectsPage?.items ?? projectsPage?.projects ?? [];
        const projectId = projectsPage
          ? resolveWorkspaceProjectId(activeProjectId, projectsPage.active_project_id, projectItems)
          : activeProjectId;
        if (cancelled) return;
        if (projectsPage) {
          setWorkspaceProjects(projectItems);
          setServerDefaultProjectId(projectsPage.server_default_project_id ?? "");
        }
        if (projectId !== activeProjectId) {
          projectRequestScope.activate(projectId);
          setActiveProjectId(projectId);
          return;
        }
        const selectedProject = projectItems.find((project) => project.project_id === projectId) ?? null;
        if (!projectId || (selectedProject && !projectCanOpenBoard(selectedProject))) {
          setSnapshot(null);
          setDeliveryFeaturesPage(null);
          setIntegrationQueue(null);
          setTaskPipeline(null);
          setRepairActions(null);
          setEvents([]);
          setChannelsPage(null);
          setError(null);
          setLiveState("connecting");
          return;
        }
        setSnapshot((current) => (
          current?.project?.project_id === projectId ? current : null
        ));
        if (owns("channels.summary")) {
          void getChannels(projectId || undefined).then((initialChannels) => {
            if (cancelled) return;
            setChannelsPage(initialChannels);
            setChannelLoadError(null);
            if (!selectedChannelId || !initialChannels.channels.some((item) => channelIdOf(item) === selectedChannelId)) {
              setSelectedChannelId(channelIdOf(initialChannels.channels[0]) || "ch-zaofu");
            }
            if (projectionNeedsFresh(initialChannels)) {
              const timers = sliceRefreshTimersRef.current;
              const key = "channels-fresh";
              const existing = timers.get(key);
              if (existing !== undefined) window.clearTimeout(existing);
              timers.set(key, window.setTimeout(() => {
                timers.delete(key);
                void getChannels(projectId || undefined, true).then((freshChannels) => {
                  if (cancelled) return;
                  setChannelsPage(freshChannels);
                  setSelectedChannelId((current) => (
                    current && freshChannels.channels.some((item) => channelIdOf(item) === current)
                      ? current
                      : channelIdOf(freshChannels.channels[0]) || "ch-zaofu"
                  ));
                }).catch(() => undefined);
              }, 2500));
            }
          }).catch((err) => {
            if (cancelled) return;
            setChannelLoadError(err instanceof Error ? err.message : String(err));
          });
        }
        if (pageLoadsDeliveryFeatures(page)) {
          void getDeliveryFeatures(projectId || undefined).then((initialFeatures) => {
            if (cancelled) return;
            setDeliveryFeaturesPage(initialFeatures);
          }).catch(() => undefined);
        }
        const [initialSnapshot, initialEventsPage, initialHealth] = await Promise.all([
          snapshotKind === "none"
            ? Promise.resolve(null)
            : snapshotKind === "full"
              ? getSnapshot(projectId || undefined)
              : getSnapshotLight(projectId || undefined),
          owns("events.recent")
            ? getRecentEventsPage(60, projectId || undefined)
            : Promise.resolve(null),
          getProjectHealth(projectId || undefined).catch(() => null),
        ]);
        if (cancelled) return;
        if (
          initialSnapshot
          && projectId
          && initialSnapshot.project?.project_id
          && initialSnapshot.project.project_id !== projectId
        ) {
          return;
        }
        const cursorFallback = initialHealth || initialEventsPage
          ? null
          : await getRecentEventsPage(1, projectId || undefined).catch(() => null);
        const initialSeq = bootstrapEventCursor({
          recentEventsSeq: initialEventsPage?.current_seq,
          snapshotSeq: initialSnapshot?.seq,
          healthSeq: initialHealth?.seq,
          fallbackSeq: cursorFallback?.current_seq,
        });
        lastSeqRef.current = initialSeq;
        if (initialSnapshot) setSnapshot(initialSnapshot);
        if (initialHealth) setProjectHealth(initialHealth);
        setEvents(initialEventsPage ? initialEventsPage.items.slice().reverse() : []);
        setError(null);
        connectStream(initialSeq, projectId);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLiveState("degraded");
      }
    }

    function connectStream(cursor: number, projectId: string) {
      eventBusRef.current?.close();
      const ticket = projectRequestScope.capture(projectId);
      const bus = new ProjectEventBus({
        cursor,
        projectId,
        shouldRefresh: (event) => shouldRefreshForEvent(event.type),
        onRefresh: (event, reason) => {
          liveRefreshRef.current?.(event, reason);
        },
        onRecoverGap: (_event, recoveryProjectId) => recoverStreamGap(recoveryProjectId),
        onStatusChange: (state) => {
          if (projectRequestScope.isCurrent(ticket)) setLiveState(state);
        },
      });
      bus.subscribe(({ event, seq }) => {
        if (!projectRequestScope.isCurrent(ticket)) return;
        if (seq) lastSeqRef.current = Math.max(lastSeqRef.current, seq);
        setEvents((current) => prependEvent(current, event));
      });
      eventBusRef.current = bus;
      bus.connect();
    }

    void bootstrap();

    return () => {
      cancelled = true;
      eventBusRef.current?.close();
      for (const timer of sliceRefreshTimersRef.current.values()) {
        window.clearTimeout(timer);
      }
      sliceRefreshTimersRef.current.clear();
    };
  }, [activeProjectId, page]);

  useEffect(() => {
    let cancelled = false;
    setTaskDetail(null);
    setTaskDiff(null);
    setTaskTimeline(null);
    setTaskTimelineLoading(false);
    setTaskTimelineError(null);
    setActionResult(null);
    setTaskLoadError(null);
    if (!selectedTaskId) return;
    const taskId = selectedTaskId;

    async function loadDetail() {
      try {
        const detail = await getTaskDetail(taskId, activeProjectId || undefined);
        if (cancelled) return;
        setTaskDetail(detail);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setTaskLoadError(message);
        setError(message);
      }
    }

    async function loadDiff() {
      try {
        const diff = await getTaskDiff(taskId, activeProjectId || undefined);
        if (cancelled) return;
        setTaskDiff(diff);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setTaskDiff({
          task_id: taskId,
          base: "",
          head: "",
          files: [],
          diff: "",
          truncated: false,
          error: message,
        });
      }
    }

    async function loadTimeline() {
      setTaskTimelineLoading(true);
      try {
        const timeline = await getTaskTimeline(taskId, activeProjectId || undefined);
        if (cancelled) return;
        setTaskTimeline(timeline);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setTaskTimelineError(message);
      } finally {
        if (!cancelled) setTaskTimelineLoading(false);
      }
    }

    void loadDetail();
    void loadDiff();
    void loadTimeline();
    return () => {
      cancelled = true;
    };
  }, [activeProjectId, selectedTaskId]);

  useProjectObservabilityData({
    activeProjectId,
    eventFilter,
    onError: setError,
    onEventsPage: setEventsPage,
    onIntegrationQueue: setIntegrationQueue,
    onRepairActions: setRepairActions,
    onTaskPipeline: setTaskPipeline,
    page,
    scope: projectRequestScope,
    selectedTaskId,
    snapshotSeq: snapshot?.seq,
  });

  useEffect(() => {
    const selection = projectionSelectionForPage(
      page,
      new URLSearchParams(window.location.search),
    );
    if (!selection || selection.kind === "trace" || !activeProjectId) return undefined;
    const navigationKey = `${activeProjectId}:${selection.kind}:${selection.id}`;
    if (projectionNavigationRef.current === navigationKey) {
      projectionNavigationRef.current = "";
      return undefined;
    }
    let cancelled = false;
    const ticket = projectRequestScope.capture(activeProjectId);
    setProjectionDetail(null);
    const request = selection.kind === "candidate"
      ? getCandidateDetail(selection.id, activeProjectId)
      : selection.kind === "fanout"
        ? getFanoutDetail(selection.id, activeProjectId)
        : getRunDetail(selection.id, activeProjectId);
    void request.then((result) => {
      if (cancelled || !projectRequestScope.isCurrent(ticket)) return;
      setProjectionDetail(result as unknown as Record<string, unknown>);
    }).catch(() => {
      if (!cancelled && projectRequestScope.isCurrent(ticket)) setProjectionDetail(null);
    });
    return () => { cancelled = true; };
  }, [activeProjectId, page, projectRequestScope]);

  useEffect(() => {
    if (page !== "channels") return;
    let cancelled = false;
    setChannelLoadError(null);
    if (!selectedChannelId) {
      setChannelDetail(null);
      return;
    }
    const detailTicket = channelDetailRequestGateRef.current.issue();
    void getChannelConversation(
      selectedChannelId,
      activeProjectId || undefined,
      { requireFresh: true },
    ).then((detail) => {
      if (!cancelled && channelDetailRequestGateRef.current.isCurrent(detailTicket)) {
        setChannelDetail((current) => mergeChannelConversationRefresh(current, detail));
      }
    }).catch((err) => {
      if (cancelled || !channelDetailRequestGateRef.current.isCurrent(detailTicket)) return;
      const message = err instanceof Error ? err.message : String(err);
      setChannelLoadError(message);
      setChannelDetail(null);
    });
    return () => {
      cancelled = true;
    };
  }, [activeProjectId, page, selectedChannelId]);

  useChannelLiveConvergence({
    activeProjectId, channelDetail, channelDetailRequestGateRef, page, projectRequestScope,
    selectedChannelId, selectedChannelIdRef, setChannelDetail,
  });

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode;
    window.localStorage.setItem("zf.themeMode", themeMode);
  }, [themeMode]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen(true);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const assignees = useMemo(() => {
    const values = new Set<string>();
    for (const task of snapshot ? allBoardTasks(snapshot) : []) {
      if (task.assigned_to) values.add(task.assigned_to);
    }
    return [...values].sort();
  }, [snapshot]);

  const skills = useMemo(() => {
    const values = new Set<string>();
    for (const task of snapshot ? allBoardTasks(snapshot) : []) {
      for (const skill of task.skills_required ?? []) {
        if (skill) values.add(skill);
      }
    }
    return [...values].sort();
  }, [snapshot]);

  const filteredTasks = useMemo(() => {
    const text = textFilter.trim().toLowerCase();
    return (snapshot ? allBoardTasks(snapshot) : []).filter((task) => {
      if (statusFilter !== "all" && taskColumn(task) !== statusFilter && task.status !== statusFilter) {
        return false;
      }
      if (assigneeFilter !== "all" && task.assigned_to !== assigneeFilter) return false;
      if (skillFilter !== "all" && !(task.skills_required ?? []).includes(skillFilter)) return false;
      if (priorityFilter !== "all" && String(taskPriority(task)) !== priorityFilter) return false;
      if (quickFilter === "focused" && !text && isNoiseTask(task)) return false;
      if (quickFilter === "blocked" && !task.blocked_reason && task.status !== "blocked") return false;
      if (quickFilter === "failed" && task.phase !== "failed" && task.phase !== "test_failed") return false;
      if (quickFilter === "ready" && !task.ready) return false;
      if (text) {
        const haystack = `${task.id} ${task.title} ${task.assigned_to ?? ""} ${task.phase ?? ""} ${task.source ?? ""}`.toLowerCase();
        if (!haystack.includes(text)) return false;
      }
      return true;
    });
  }, [assigneeFilter, priorityFilter, quickFilter, skillFilter, snapshot, statusFilter, textFilter]);

  const tasksByColumn = useMemo(() => {
    const grouped: Record<BoardColumnId, Task[]> = {
      ready: [],
      in_progress: [],
      testing: [],
      blocked: [],
      done: [],
    };

    for (const task of filteredTasks) {
      grouped[taskColumn(task)].push(task);
    }
    for (const column of BOARD_COLUMNS) {
      grouped[column.id].sort((left, right) => taskPriority(left) - taskPriority(right));
    }

    return grouped;
  }, [filteredTasks]);

  const activeFanouts = useMemo(() => {
    const active = new Set(["requested", "started", "running", "aggregating"]);
    return (snapshot?.fanouts ?? []).filter((fanout) => (
      active.has(textValue(fanout.status).toLowerCase())
    ));
  }, [snapshot]);

  const orchestratorContext = useMemo<OrchestratorContext>(() => {
    const projection = projectionDetail ?? {};
    return {
      taskId: selectedTask?.id ?? selectedTaskId ?? "",
      traceId: taskDetail?.links?.trace || taskDetail?.trace_id || selectedTask?.links?.trace || "",
      pddId: taskDetail?.links?.candidate || selectedTask?.links?.candidate || "",
      fanoutId: taskDetail?.links?.fanout || selectedTask?.links?.fanout || "",
      stageId: page === "fanouts" ? textValue(projection.stage_id) : "",
      targetRef: page === "fanouts" ? textValue(projection.target_ref) : "",
      title: selectedTask?.title ?? "",
    };
  }, [page, projectionDetail, selectedTask, selectedTaskId, taskDetail]);

  async function runSearch() {
    const requestedProjectId = activeProjectId || "";
    const ticket = projectRequestScope.capture(requestedProjectId);
    const result = await search(searchQuery, 80, requestedProjectId || undefined);
    if (!projectRequestScope.isCurrent(ticket)) return;
    setSearchResult(result);
    setPage("events");
  }

  async function submitAction(action: string, payload: Record<string, unknown>) {
    const requestedProjectId = activeProjectId || "";
    const ticket = projectRequestScope.capture(requestedProjectId);
    const result = await postAction(action, payload, requestedProjectId || undefined);
    if (!projectRequestScope.isCurrent(ticket)) return result;
    if (actionFailed(result) && actionFailureReason(result).includes("missing or invalid web action token/session")) {
      window.localStorage.removeItem("zf.webActionToken");
      setWebActionTokenPresent(false);
    }
    // Polls update their own component, not every projection on the page.
    if (actionIsReadOnlySelfIssue(action)) return result;
    setActionResult(result);
    // Do not hold the action response on an unrelated history refresh. A
    // Self-Issue capture is already reflected by its action result; waiting
    // for a large event projection made the composer spin for minutes and
    // caused a second overlapping refresh during memory pressure.
    const eventRefresh = getRecentEvents(60, requestedProjectId || undefined)
      .then((recent) => {
        if (projectRequestScope.isCurrent(ticket)) setEvents(recent.slice().reverse());
      })
      .catch(() => undefined);
    if (!actionEventRefreshRunsInBackground(action)) await eventRefresh;
    if (!projectRequestScope.isCurrent(ticket)) return result;
    if (shouldRefreshChannelsAfterAction(action, result)) {
      void loadChannels();
    } else {
      void refresh();
    }
    return result;
  }

  async function runTaskAction(action: string, payload: Record<string, unknown> = {}) {
    const taskId = String(payload.task_id || selectedTaskId || "");
    if (!taskId) return;
    await submitAction(action, { ...payload, task_id: taskId });
  }

  async function moveBoardTaskStatus(taskId: string, status: BoardColumnId) {
    await submitAction("update-task", { task_id: taskId, status });
  }

  async function createChannelFromDraft() {
    const name = newChannelDraft.name.trim();
    if (!name || name === "#") return;
    const result = await submitAction("channel.create", {
      name,
      channel_id: newChannelDraft.channelId.trim() || undefined,
      source: "web-new-channel-draft",
    });
    const channelId = String(result.channel_id || newChannelDraft.channelId || "").trim();
    if (channelId) {
      setSelectedChannelId(channelId);
      setPage("channels");
    }
    setNewChannelDraft(emptyNewChannelDraft());
    setNewChannelOpen(false);
  }

  async function validateProjectWizardPath() {
    const root = projectWizardDraft.root.trim();
    if (!root) return;
    setProjectWizardBusy(true);
    setProjectWizardError("");
    try {
      const result = await validateWorkspaceProjectPath(root);
      setProjectWizardInspection(result);
      if (result.admission.action === "initialize_project") {
        setProjectWizardDraft((current) => (
          current.root.trim() === root && !current.name.trim()
            ? { ...current, name: projectNameFromPath(result.root_resolved) }
            : current
        ));
      }
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      if (reason.includes("missing or invalid web action token/session")) {
        window.localStorage.removeItem("zf.webActionToken");
        setWebActionTokenPresent(false);
      }
      setProjectWizardInspection(null);
      setProjectWizardError(reason);
    } finally {
      setProjectWizardBusy(false);
    }
  }

  async function submitProjectWizard() {
    const root = projectWizardDraft.root.trim();
    const action = projectWizardInspection?.admission.action;
    if (!root || !action || action === "blocked") return;
    setProjectWizardBusy(true);
    setProjectWizardError("");
    try {
      if (action === "open") {
        const projectId = projectWizardInspection.admission.project_id;
        if (!projectId) throw new Error("registered project id is missing");
        switchProject(projectId);
        setProjectWizardOpen(false);
        return;
      }

      const result = action === "register"
        ? await registerWorkspaceProject({ root, workspace: "default" })
        : await initWorkspaceProject({
          root,
          workspace: "default",
          ...(action === "initialize_project" ? {
            name: projectWizardDraft.name.trim(),
            description: projectWizardDraft.description.trim() || undefined,
            stack: projectWizardDraft.stack || undefined,
            backend: projectWizardDraft.backend,
            mixed_enabled: projectWizardDraft.mixedEnabled,
          } : {}),
        });
      if (actionFailed(result)) {
        const reason = actionFailureReason(result) || "project action failed";
        if (reason.includes("missing or invalid web action token/session")) {
          window.localStorage.removeItem("zf.webActionToken");
          setWebActionTokenPresent(false);
        }
        setProjectWizardError(reason);
        return;
      }

      const project = recordValue(result.project);
      const projectId = textValue(project?.project_id).trim();
      if (!projectId) throw new Error("project action did not return a project id");
      await loadWorkspaceProjects();
      switchProject(projectId);
      setProjectWizardOpen(false);
    } catch (err) {
      setProjectWizardError(err instanceof Error ? err.message : String(err));
    } finally {
      setProjectWizardBusy(false);
    }
  }

  async function initializeActiveProject() {
    if (!activeProject) return;
    const result = await initWorkspaceProject({
      root: activeProject.root,
      workspace: "default",
    });
    setActionResult(result as unknown as ActionResponse);
    await loadWorkspaceProjects();
    await refresh();
  }

  async function submitChannelMessage(
    text: string,
    refs?: Record<string, unknown>,
    ingress?: {
      clientMessageId: string;
      threadId: string;
      replyToMessageId?: string;
    },
  ) {
    const channelId = selectedChannelId || "ch-zaofu";
    const projectId = activeProjectId || undefined;
    const ticket = projectRequestScope.capture(projectId || "");
    let refreshTimer: ReturnType<typeof window.setInterval> | null = null;
    let refreshBusy = false;
    async function refreshChannelProjection() {
      const detailTicket = channelDetailRequestGateRef.current.issue();
      const [detail, recent] = await Promise.all([
        getChannelConversation(channelId, projectId, { requireFresh: true }),
        getRecentEvents(60, projectId),
      ]);
      if (!projectRequestScope.isCurrent(ticket)) return;
      if (selectedChannelIdRef.current === channelId
        && channelDetailRequestGateRef.current.isCurrent(detailTicket)) {
        setChannelDetail((current) => mergeChannelConversationRefresh(current, detail));
      }
      setEvents(recent.slice().reverse());
    }
    function refreshWhilePosting() {
      if (refreshBusy) return;
      refreshBusy = true;
      void refreshChannelProjection().catch(() => {
        // The action request is the authoritative path; polling only keeps
        // the channel timeline responsive while long @ALL runs are active.
      }).finally(() => {
        refreshBusy = false;
      });
    }
    refreshTimer = window.setInterval(refreshWhilePosting, 2500);
    refreshWhilePosting();
    try {
      const result = await postChannelMessage(channelId, {
        thread_id: ingress?.threadId || "main",
        text,
        member_id: "operator",
        role: "user",
        source: "web-channel-composer",
        client_message_id: ingress?.clientMessageId,
        idempotency_key: ingress?.clientMessageId,
        reply_to_message_id: ingress?.replyToMessageId,
        ...(refs ? { refs } : {}),
      }, projectId);
      if (!projectRequestScope.isCurrent(ticket)) return;
      setActionResult(result);
      if (!result.ok) {
        throw new Error(result.reason || result.status || "channel message failed");
      }
      try {
        await refreshChannelProjection();
      } catch {
        // SSE/refresh remain the primary live path; composer submit should not
        // fail after the deterministic action has been accepted.
      }
      void loadChannels();
    } finally {
      if (refreshTimer) window.clearInterval(refreshTimer);
    }
  }

  const channelActions = createChannelActionAdapter({
    activeProjectId,
    channelDetail: channelDiagnosticsDetail ?? channelDetail,
    prepareTaskAgent: (taskId) => {
      if (taskId) setSelectedTaskId(taskId);
      openTaskAgent();
    },
    readStoredBackend: () => (
      typeof window === "undefined"
        ? ""
        : window.localStorage.getItem("zf.operatorBackend") ?? ""
    ),
    refreshChannelDetail: async () => {
      const channelId = selectedChannelId || "ch-zaofu";
      const projectId = activeProjectId || undefined;
      const projectTicket = projectRequestScope.capture(projectId || "");
      const detailTicket = channelDetailRequestGateRef.current.issue();
      const detail = await getChannelConversation(channelId, projectId, {
        requireFresh: true,
      });
      if (!projectRequestScope.isCurrent(projectTicket)) return;
      if (selectedChannelIdRef.current !== channelId) return;
      if (!channelDetailRequestGateRef.current.isCurrent(detailTicket)) return;
      setChannelDetail((current) => mergeChannelConversationRefresh(current, detail));
    },
    selectedChannelId,
    snapshot,
    submitAction,
  });

  async function drainChannelReplies() {
    await submitAction("channel-drain-replies", {
      channel_id: selectedChannelId || "ch-zaofu",
      allow_queued: true,
      source: "web-channel-drain",
    });
  }

  async function clearChannelHistory(threadId: string) {
    const channelId = selectedChannelId || "ch-zaofu";
    if (!window.confirm(`Clear visible history for ${channelId}?`)) return;
    await channelActions.clearHistory(threadId);
  }

  async function runChannelHistorySearch(q: string, threadId?: string): Promise<ChannelHistorySearchResult> {
    return searchChannelHistory(
      selectedChannelId || "ch-zaofu",
      q,
      activeProjectId || undefined,
      { limit: 30, threadId },
    );
  }

  async function markChannelRead(threadId: string) {
    await submitAction("channel-mark-read", {
      channel_id: selectedChannelId || "ch-zaofu",
      thread_id: threadId || "main",
      member_id: "operator",
      source: "web-channel-read-state",
    });
  }

  async function pinChannelMessage(
    messageId: string,
    threadId: string,
    pinned: boolean,
  ) {
    await submitAction("channel-pin-message", {
      channel_id: selectedChannelId || "ch-zaofu",
      thread_id: threadId || "main",
      message_id: messageId,
      member_id: "operator",
      pinned,
      source: "web-channel-message-action",
    });
  }

  async function deleteChannel() {
    const channelId = selectedChannelId || "ch-zaofu";
    if (!window.confirm(`Delete channel ${channelId}?`)) return;
    await submitAction("channel-delete", {
      channel_id: channelId,
      thread_id: "main",
      reason: "deleted from channel settings",
      source: "web-channel-settings",
    });
    const fallback = (channelsPage?.channels ?? [])
      .map((channel) => channelIdOf(channel))
      .find((id) => id && id !== channelId) || "ch-zaofu";
    setSelectedChannelId(fallback);
  }

  async function removeChannelMember(memberId: string) {
    const channelId = selectedChannelId || "ch-zaofu";
    if (!memberId) return;
    if (!window.confirm(`Remove ${memberId} from ${channelId}?`)) return;
    await submitAction("channel-remove-member", {
      channel_id: channelId,
      thread_id: "main",
      member_id: memberId,
      reason: "removed from channel members drawer",
      source: "web-channel-members",
    });
  }

  async function setChannelMemberPermission(memberId: string, permissionProfile: ChannelPermissionProfile) {
    const channelId = selectedChannelId || "ch-zaofu";
    if (!memberId) return;
    if (permissionProfile === "project_writer") {
      const ok = window.confirm(`Allow ${memberId} to create and edit project files in ${channelId}?`);
      if (!ok) return;
    }
    await submitAction("channel.member.permission", {
      channel_id: channelId,
      thread_id: "main",
      member_id: memberId,
      permission_profile: permissionProfile,
      reason: `set ${permissionProfile} from channel members drawer`,
      source: "web-channel-members",
    });
  }

  async function unlockSession(passcode: string) {
    const result = await unlockWebSession(passcode);
    if (result.session) setWebSessionStatus(result.session);
    setActionResult({
      ok: result.ok,
      status: result.status,
      action: "web-session",
      reason: result.reason || (result.ok ? "session unlocked" : "session locked"),
    });
    await refresh();
    return result;
  }

  async function lockSession() {
    const result = await lockWebSession();
    if (result.session) setWebSessionStatus(result.session);
    setActionResult({
      ok: result.ok,
      status: result.status,
      action: "web-session",
      reason: "session locked",
    });
    await refresh();
  }

  function openTask(taskId: string) {
    setSelectedTaskId(taskId);
    setDetailTab("Summary");
    setPage("task");
  }

  function openChannel(channelId: string) {
    setSelectedChannelId(channelId || "zaofu");
    setPage("channels");
  }

  function backToBoard() {
    setPage("board");
  }

  function saveWebActionToken(token: string) {
    const trimmed = normalizeWebActionToken(token);
    if (trimmed) {
      window.localStorage.setItem("zf.webActionToken", trimmed);
      setWebActionTokenPresent(true);
      return;
    }
    window.localStorage.removeItem("zf.webActionToken");
    setWebActionTokenPresent(false);
  }

  function openProjectWizard() {
    setProjectWizardDraft(emptyProjectWizardDraft(onboardingStatus));
    setProjectWizardInspection(null);
    setProjectWizardError("");
    setProjectWizardBusy(false);
    setProjectWizardOpen(true);
  }

  function openTaskAgent() {
    setAgentPanelHasOpened(true);
    setAgentPanelMode((mode) => (mode === "fullscreen" ? "fullscreen" : "docked"));
    setOrchestratorFocusSignal((value) => value + 1);
    if (!snapshot && activeProjectId) {
      void loadSnapshot("light");
    }
    if (!events.length && activeProjectId) {
      const ticket = projectRequestScope.capture(activeProjectId);
      void getRecentEvents(60, activeProjectId).then((recent) => {
        if (projectRequestScope.isCurrent(ticket)) setEvents(recent.slice().reverse());
      }).catch(() => undefined);
    }
  }

  function openAddAgentModal() {
    if (channelsPage) {
      setAddAgentOpen(true);
      return;
    }
    void loadChannels().then(() => setAddAgentOpen(true)).catch((err) => {
      setChannelLoadError(err instanceof Error ? err.message : String(err));
    });
  }

  async function submitAddAgentToChannel() {
    const memberId = addAgentDraft.memberId.trim();
    if (!memberId) return;
    const permissions = [
      "read",
      addAgentDraft.canMessage ? "message" : "",
      addAgentDraft.canSummarize ? "summarize" : "",
      addAgentDraft.canProposeWorkflow ? "propose_workflow" : "",
    ].filter(Boolean);
    await submitAction("channel.add_member", {
      channel_id: selectedChannelId,
      name: channelNameOf(channelDetail ?? channelsPage?.channels.find((item) => channelIdOf(item) === selectedChannelId)),
      member_id: memberId,
      member_type: addAgentDraft.memberType,
      provider: addAgentDraft.provider.trim() || addAgentDraft.backend.trim() || undefined,
      backend: addAgentDraft.backend.trim() || undefined,
      provider_binding_id: addAgentDraft.providerBindingId.trim() || undefined,
      channel_role: addAgentDraft.channelRole,
      visibility_profile: addAgentDraft.visibilityProfile,
      role_context_ref: addAgentDraft.roleContextRef.trim() || undefined,
      skill_refs: csvList(addAgentDraft.skillRefs),
      persona: memberId,
      scope: addAgentDraft.scope.trim() || "channel",
      permission_profile: addAgentDraft.permissionProfile,
      dangerous_ack: addAgentDraft.permissionProfile === "dangerous_full" ? addAgentDraft.dangerousAck : undefined,
      permissions,
      reason: addAgentDraft.reason.trim() || "added from channel detail",
    });
    setAddAgentDraft(emptyAddAgentDraft());
    setAddAgentOpen(false);
  }

  async function openProjection(kind: ProjectionKind, id: string) {
    const requestedProjectId = activeProjectId || "";
    const ticket = projectRequestScope.capture(requestedProjectId);
    const target = projectionPageQuery(new URLSearchParams(window.location.search), kind, id);
    window.history.replaceState(null, "", `?${target.params.toString()}`);
    setPage(target.page as PageId);
    if (kind === "trace") {
      setProjectionDetail(null);
      return;
    }
    projectionNavigationRef.current = `${requestedProjectId}:${kind}:${id}`;
    const result = kind === "candidate"
      ? await getCandidateDetail(id, requestedProjectId || undefined)
      : kind === "fanout"
        ? await getFanoutDetail(id, requestedProjectId || undefined)
        : await getRunDetail(id, requestedProjectId || undefined);
    if (!projectRequestScope.isCurrent(ticket)) return;
    setProjectionDetail(result as unknown as Record<string, unknown>);
  }

  function switchProject(projectId: string) {
    if (!projectId || projectId === activeProjectId) return;
    projectRequestScope.activate(projectId);
    setActiveProjectId(projectId);
    void touchWorkspaceProject(projectId).then(() => loadWorkspaceProjects()).catch(() => {
      // Project selection must not fail just because recent-project metadata
      // could not be written.
    });
    setSelectedTaskId(null);
    setTaskDetail(null);
    setTaskDiff(null);
    setTaskTimeline(null);
    setTaskTimelineLoading(false);
    setTaskTimelineError(null);
    setProjectionDetail(null);
    setIntegrationQueue(null);
    setTaskPipeline(null);
    setRepairActions(null);
    setSearchResult(null);
    setEventFilter("");
    setEventsPage(null);
    setEvents([]);
    setSnapshot(null);
    setChannelDetail(null);
    setChannelsPage(null);
    setSelectedChannelId("");
    setPage("project");
  }

  async function removeSelectedProject() {
    if (!activeProjectId) return;
    if (activeProjectId === serverDefaultProjectId) {
      setError("Can't remove the project the server was started with — it is re-registered on every refresh. Restart `zf web` (or use `--workspace-only`) to drop it.");
      return;
    }
    let result: Record<string, unknown>;
    try {
      result = await removeWorkspaceProject(activeProjectId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return;
    }
    setActionResult(result as unknown as ActionResponse);
    if (result.ok === false) {
      setError(String(result.reason || result.status || "remove failed"));
      return;
    }
    const pageData = await loadWorkspaceProjects();
    const items = pageData.items ?? pageData.projects ?? [];
    const nextProject = items.find((project) => project.project_id !== activeProjectId);
    if (nextProject) {
      switchProject(nextProject.project_id);
    } else {
      projectRequestScope.activate("");
      setActiveProjectId("");
      setSnapshot(null);
      setDeliveryFeaturesPage(null);
      setChannelsPage(null);
      setEvents([]);
      setPage("project");
    }
  }

  const topbarStatus = connectionStatusView({
    activeProjectId,
    error,
    liveState,
    snapshot,
    snapshotRequired: pageLoadsSnapshot(page),
  });
  if (projectHealth?.runtime_state === "archived") {
    topbarStatus.className = "status-idle";
    topbarStatus.label = "archived";
    topbarStatus.title = "Archived project: historical record, no live runtime.";
  }
  const topbarProjectName = snapshot?.project.name || activeProject?.name || projectLabelFromId(activeProjectId) || "ZaoFu Project";
  const sliceContextLabel = page === "channels" && channelsPage
    ? "channels slice"
    : pageLoadsDeliveryFeatures(page) && deliveryFeaturesPage
      ? "measure slice"
      : page === "agents"
        ? "agent slice"
        : "slice pending";
  const topbarContextLabel = snapshot
    ? "local workspace"
    : activeProjectId
      ? sliceContextLabel
      : "no project";
  const agentPanelVisible = agentPanelMode !== "collapsed";
  const renderedAgentPanelMode: Exclude<AgentPanelMode, "collapsed"> = agentPanelMode === "fullscreen"
    ? "fullscreen"
    : "docked";

  if (showWelcome) {
    return (
      <WelcomeWizard
        accessReady={actionGate.actionReady}
        tokenPresent={webActionTokenPresent}
        onSaveToken={saveWebActionToken}
        onDone={() => {
          setShowWelcome(false);
          void getOnboarding().then(setOnboardingStatus).catch(() => undefined);
          void loadWorkspaceProjects();
        }}
      />
    );
  }

  return (
    <div className={`app-shell ${page === "channels" ? "app-shell-channel" : ""}`.trim()}>
      <header className="topbar">
        <div className="brand-block">
          <h1>{topbarProjectName}</h1>
          <span className="breadcrumb-separator">/</span>
          <span className="breadcrumb-current">{pageTitle(page)}</span>
          <span className="muted">{topbarContextLabel}</span>
        </div>
        <div className="status-row">
          <div className="topbar-search-control">
            <input
              className="search-input"
              aria-label="Search workspace"
              placeholder="task:TASK-123 actor:dev-1"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void runSearch();
              }}
            />
            <button
              className="topbar-search-button"
              type="button"
              aria-label={uiText("Search")}
              title="Search workspace"
              onClick={() => void runSearch()}
            >
              <Search size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
          </div>
          <div className="topbar-runtime-cluster">
            <div className="topbar-runtime-summary" aria-label="Dashboard data status">
              <span className={`status-pill ${topbarStatus.className}`} title={topbarStatus.title}>{topbarStatus.label}</span>
              {projectHealth ? (
                <span className="topbar-activity mono" title={`snap #${projectHealth.seq} · projection ${projectHealth.projection?.state ?? "-"}`}>
                  {projectHealth.active} active{projectHealth.queued > 0 ? ` · ${projectHealth.queued} queued` : ""} · {projectHealth.blocked} blocked
                </span>
              ) : null}
            </div>
            <button
              className="topbar-tool-button topbar-refresh-button"
              type="button"
              aria-label={uiText("Refresh")}
              title="Refresh dashboard"
              onClick={() => void refresh()}
            >
              <RefreshCw size={16} strokeWidth={1.9} aria-hidden="true" />
            </button>
          </div>
          <span className="topbar-tool-separator" aria-hidden="true" />
          <button
            className="topbar-tool-button topbar-terminal-button topbar-terminal-launch"
            disabled={!activeProjectId}
            type="button"
            aria-label="Toggle Terminal"
            aria-pressed={terminalOpen}
            title={terminalOpen ? "Close terminal" : "Open terminal"}
            onClick={() => setTerminalOpen((value) => !value)}
          >
            <Terminal size={16} strokeWidth={1.9} aria-hidden="true" />
          </button>
        </div>
      </header>

      {error ? <div className="error-strip">{error}</div> : null}

      <main className={`workspace ${terminalOpen && activeProjectId ? "has-web-terminal" : ""}`.trim()}>
        <WorkspaceRail
          actionResult={actionResult}
          activePage={page}
          activeProjectId={activeProjectId}
          canViewIssueTriage={developerAccess.mode === "developer" && developerAccess.verified && actionGate.actionReady && Boolean(webSessionStatus?.can_view_issue_triage)}
          channels={channelsPage?.channels ?? []}
          channelsLoaded={channelsPage !== null}
          inboxPendingCount={projectHealth?.runtime_state === "archived" ? 0 : inboxPendingCount}
          liveState={liveState}
          onAddProject={() => openProjectWizard()}
          onOpenChannel={openChannel}
          onOpenPage={(nextPage) => setPage(nextPage)}
          onNewChannel={() => setNewChannelOpen(true)}
          onRemoveProject={() => void removeSelectedProject()}
          onSelectProject={switchProject}
          projects={workspaceProjects}
          removeDisabled={Boolean(activeProjectId) && activeProjectId === serverDefaultProjectId}
          selectedChannelId={selectedChannelId}
          snapshot={snapshot}
        />

        <section className="board-panel" id="self-issue-workspace-host">
          {!activeProjectId ? (
            <WorkspaceEmptyPanel onAddProject={() => openProjectWizard()} />
          ) : activeProject && !activeProjectReady ? (
            <ProjectInitPanel
              actionReady={webActionTokenPresent}
              onAddProject={() => openProjectWizard()}
              onInit={() => void initializeActiveProject()}
              project={activeProject}
            />
          ) : page === "project" ? (
            <ProjectHomePage
              onOpenPage={setPage}
              projectId={activeProjectId}
              snapshot={snapshot}
              onOpenTask={openTask}
              onOpenProjection={(kind, id) => void openProjection(kind, id)}
            />
          ) : page === "inbox" ? (
            <PlanApprovalPanel projectId={activeProjectId} autoOpenPlanId={initial.plan} />
          ) : page === "channels" ? (
              <ChannelRoute
                actionReady={actionGate.actionReady}
                actionResult={actionResult}
                channels={channelsPage?.channels ?? []}
                detail={channelDetail}
                diagnosticsDetail={channelDiagnosticsDetail}
                diagnosticsError={channelDiagnosticsError}
                diagnosticsLoading={channelDiagnosticsLoading}
                events={events}
                loadError={channelLoadError}
                onAddAgent={openAddAgentModal}
                onNewChannel={() => setNewChannelOpen(true)}
                onOpenChannel={openChannel}
                onPostMessage={(text, refs, ingress) => submitChannelMessage(text, refs, ingress)}
                onDrainReplies={() => drainChannelReplies()}
                onGenerateOwnerReport={(threadId) => channelActions.generateOwnerReport(threadId)}
                onClearHistory={(threadId) => clearChannelHistory(threadId)}
                onDeleteChannel={() => deleteChannel()}
                onMarkRead={(threadId) => markChannelRead(threadId)}
                onLoadDiagnostics={loadChannelDiagnostics}
                onLoadEarlier={loadEarlierChannelMessages}
                onPinMessage={(messageId, threadId, pinned) => (
                  pinChannelMessage(messageId, threadId, pinned)
                )}
                onSearchHistory={(query, threadId) => runChannelHistorySearch(query, threadId)}
                onRequestSynthesis={(threadId, targetMemberId) => channelActions.requestSynthesis(threadId, targetMemberId)}
                onResolveQuestion={channelActions.resolveQuestion}
                onSetDiscussionMode={(threadId, mode, defaultResponderId) => channelActions.setDiscussionMode(threadId, mode, defaultResponderId)}
                onStartDiscussion={(threadId, message, messageId, mode, restart) => channelActions.startDiscussion(threadId, message, messageId, mode, restart)}
                onConsensusDecision={channelActions.decideConsensus}
                onRemoveMember={(memberId) => removeChannelMember(memberId)}
                onResearchAdopt={channelActions.adoptResearchResult}
                onSetMemberPermission={(memberId, permissionProfile) => setChannelMemberPermission(memberId, permissionProfile)}
                onWorkflowRequest={channelActions.submitWorkflowRequest}
                selectedChannelId={selectedChannelId}
              />
          ) : page === "triage" ? (
            <TriagePage
              actionResult={actionResult}
              events={events}
              pendingProposals={kanbanPendingProposals}
              onAction={(action, payload) => void submitAction(action, payload)}
              onOpenTask={openTask}
              tasks={snapshot ? allBoardTasks(snapshot) : []}
            />
          ) : page === "issue-triage" ? (
            developerAccess.mode === "developer" && developerAccess.verified && webSessionStatus?.can_view_issue_triage === true ? (
              <Suspense fallback={<section className="subsection"><p className="muted">{uiText("Loading Issue Triage…")}</p></section>}>
                <IssueTriagePage
                  projectId={activeProjectId}
                  tab={issueTriageTab}
                  onTabChange={setIssueTriageTab}
                  onOpenTask={openTask}
                  onOpenProjection={(kind, id) => void openProjection(kind, id)}
                  runtimeInterventions={(
                    <TriagePage
                      actionResult={actionResult}
                      events={events}
                      pendingProposals={kanbanPendingProposals}
                      onAction={(action, payload) => void submitAction(action, payload)}
                      onOpenTask={openTask}
                      tasks={snapshot ? allBoardTasks(snapshot) : []}
                    />
                  )}
                />
              </Suspense>
            ) : (
              <section className="subsection" aria-label="Triage access">
                <p className="muted">{uiText("Checking developer Triage access…")}</p>
              </section>
            )
          ) : page === "board" && !snapshot ? (
            /* P0-B: loading must not impersonate an empty board ("0 tasks" for
               2s then 9 blocked appears — the trust-killer zero). */
            <section className="subsection">
              <p className="muted">{uiText("Loading board…")}</p>
            </section>
          ) : page === "board" ? (
            <BoardWorkbench
              actionReady={actionGate.actionReady}
              actionResult={actionResult}
              actionState={actionGate.actionState}
              activeFanouts={activeFanouts}
              agents={snapshot?.agents ?? []}
              assignees={assignees}
              assigneeFilter={assigneeFilter}
              filteredTasks={filteredTasks}
              mutationEnabled={actionGate.mutationEnabled}
              quickFilter={quickFilter}
              skillFilter={skillFilter}
              skills={skills}
              priorityFilter={priorityFilter}
              selectedTaskId={selectedTaskId}
              setAssigneeFilter={setAssigneeFilter}
              setSkillFilter={setSkillFilter}
              setPriorityFilter={setPriorityFilter}
              setQuickFilter={setQuickFilter}
              showTokenRow={actionGate.showTokenRow}
              onOpenTask={openTask}
              onSaveToken={saveWebActionToken}
              onUnlockSession={unlockSession}
              passcodeRequired={actionGate.passcodeRequired}
              setStatusFilter={setStatusFilter}
              setTextFilter={setTextFilter}
              setViewMode={setViewMode}
              statusFilter={statusFilter}
              tasksByColumn={tasksByColumn}
              textFilter={textFilter}
              viewMode={viewMode}
              onMoveTaskStatus={(taskId, status) => void moveBoardTaskStatus(taskId, status)}
              onOpenFanout={(fanoutId) => void openProjection("fanout", fanoutId)}
              totalTaskCount={snapshot ? allBoardTasks(snapshot).length : 0}
            />
          ) : page === "task" ? (
            <TaskDetail
              actionReady={actionGate.actionReady}
              actionResult={actionResult}
              actionState={actionGate.actionState}
              detail={taskDetail}
              diff={taskDiff}
              events={events}
              loadError={taskLoadError}
              onAction={(action, payload) => void runTaskAction(action, payload)}
              onBackToBoard={backToBoard}
              onOpenOrchestrator={openTaskAgent}
              onOpenProjection={(kind, id) => void openProjection(kind, id)}
              selectedTaskId={selectedTaskId}
              setTab={setDetailTab}
              skillsSummary={snapshot?.skills ?? null}
              tab={detailTab}
              task={selectedTask}
              timeline={taskTimeline}
              timelineError={taskTimelineError}
              timelineLoading={taskTimelineLoading}
            />
          ) : page === "traces" ? (
            <section className="projection-scroll">
              <TracesRoute projectId={activeProjectId} liveState={liveState} />
            </section>
          ) : (
            <section className={page === "settings" ? "projection-scroll settings-host" : "projection-scroll"}>
                <ProjectionRoute
                  activeProjectId={activeProjectId}
                  eventsPage={eventsPage}
                  eventFilter={eventFilter}
                  liveState={liveState}
                  recentEvents={events}
                  integrationQueue={integrationQueue}
                  taskPipeline={taskPipeline}
                  page={page}
                  projectionDetail={projectionDetail}
                  repairActions={repairActions}
                  searchResult={searchResult}
                  selectedTaskId={selectedTaskId}
                  setEventFilter={setEventFilter}
                  channels={channelsPage?.channels ?? []}
                  snapshot={snapshot}
                  deliveryFeaturesPage={deliveryFeaturesPage}
                  themeMode={themeMode}
                  actionReady={actionGate.actionReady}
                  actionState={actionGate.actionState}
                  onOpenChannel={openChannel}
                  onAddAgentToChannel={(agent) => {
                    setAddAgentDraft({
                      ...emptyAddAgentDraft(),
                      memberId: agent.instance_id,
                      memberType: "provider_agent",
                      provider: agent.backend.includes("codex")
                        ? "codex"
                        : agent.backend === "claude" || agent.backend === "claude-code"
                          ? "claude-code"
                          : "runtime-role",
                      channelRole: agent.role_kind === "reviewer" ? "dev_reviewer" : "tech_leader",
                      visibilityProfile: agent.role_kind === "reviewer" ? "reviewer" : "planner",
                      backend: agent.backend || "",
                      reason: "added from agent roster",
                    });
                    openAddAgentModal();
                  }}
                  onThemeModeChange={setThemeMode}
                  onOpenPage={(nextPage) => setPage(nextPage)}
                  onOpenProjection={(kind, id) => void openProjection(kind, id)}
                  onAction={submitAction}
                  onClearTaskScope={() => {
                    setSelectedTaskId(null);
                    setEventFilter("");
                  }}
                  onSelectTask={openTask}
                />
            </section>
          )}
        </section>
        {terminalOpen && activeProjectId ? (
          <Suspense fallback={<div className="web-terminal-loading">{uiText("Loading terminal UI…")}</div>}>
            <TerminalDrawer
              key={activeProjectId}
              projectId={activeProjectId}
              themeMode={themeMode}
              onClose={() => setTerminalOpen(false)}
            />
          </Suspense>
        ) : null}
      </main>
      {agentPanelMode === "collapsed" ? (
        <button
          className="agent-fab"
          type="button"
          aria-label="Open Kanban Agent"
          onClick={openTaskAgent}
        >
          <MessageCircle size={20} strokeWidth={1.9} aria-hidden="true" />
        </button>
      ) : null}
      {/* Keep the orchestrator mounted in the background so an automatically
          detected /issue can surface its intake card without requiring the
          operator to open Kanban Agent first. The shell remains hidden until
          explicitly opened. */}
      <div
          aria-hidden={agentPanelVisible ? undefined : true}
          className={`agent-page-shell ${renderedAgentPanelMode}`}
          hidden={!agentPanelVisible}
          role="presentation"
        >
            <OrchestratorRoute
              actionResult={actionResult}
              activeProjectId={activeProjectId}
              context={orchestratorContext}
              events={events}
              focusSignal={orchestratorFocusSignal}
              panelMode={renderedAgentPanelMode}
              visible
              onAction={submitAction}
              onPanelModeChange={setAgentPanelMode}
              onLockSession={() => void lockSession()}
              onSaveToken={saveWebActionToken}
              onUnlockSession={unlockSession}
              snapshot={snapshot}
              tokenPresent={webActionTokenPresent}
            />
      </div>
      {newChannelOpen ? (
        <NewChannelModal
          actionReady={actionGate.actionReady}
          draft={newChannelDraft}
          onClose={() => setNewChannelOpen(false)}
          onDraftChange={setNewChannelDraft}
          onSubmit={() => void createChannelFromDraft()}
        />
      ) : null}
      {addAgentOpen ? (
        <AddAgentModal
          actionReady={actionGate.actionReady}
          channels={channelsPage?.channels ?? []}
          draft={addAgentDraft}
          onChannelChange={setSelectedChannelId}
          onClose={() => setAddAgentOpen(false)}
          onDraftChange={setAddAgentDraft}
          onSubmit={() => void submitAddAgentToChannel()}
          selectedChannelId={selectedChannelId}
          skillOptions={skillRefOptions(snapshot?.skills)}
        />
      ) : null}
      {projectWizardOpen ? (
        <ProjectAdmissionModal
          accessMode={
            actionGate.passcodeRequired
              ? "passcode"
              : actionGate.mutationEnabled
                ? "token"
                : "unavailable"
          }
          actionReady={actionGate.actionReady}
          availableBackends={onboardingStatus?.backends ?? []}
          busy={projectWizardBusy}
          draft={projectWizardDraft}
          error={projectWizardError}
          inspection={projectWizardInspection}
          mixedAvailable={Boolean(onboardingStatus?.mixed_available)}
          onClose={() => setProjectWizardOpen(false)}
          onAuthorize={async (credential) => {
            if (actionGate.passcodeRequired) {
              const result = await unlockSession(credential);
              return { ok: result.ok, reason: result.reason };
            }
            saveWebActionToken(credential);
            return { ok: true };
          }}
          onDraftChange={(draft) => {
            const rootChanged = draft.root !== projectWizardDraft.root;
            setProjectWizardDraft(draft);
            if (rootChanged) setProjectWizardInspection(null);
            setProjectWizardError("");
          }}
          onInspect={() => void validateProjectWizardPath()}
          onSubmit={() => void submitProjectWizard()}
        />
      ) : null}
      {commandOpen ? (
        <CommandPalette
          actionReady={actionGate.actionReady}
          canViewIssueTriage={developerAccess.mode === "developer" && developerAccess.verified && actionGate.actionReady && Boolean(webSessionStatus?.can_view_issue_triage)}
          events={events}
          onAction={(action, payload) => void submitAction(action, payload)}
          onClose={() => setCommandOpen(false)}
          onOpenAgent={() => {
            setCommandOpen(false);
            openTaskAgent();
          }}
          onOpenPage={(nextPage) => {
            setCommandOpen(false);
            setPage(nextPage);
          }}
          onOpenProjection={(kind, id) => {
            setCommandOpen(false);
            void openProjection(kind, id);
          }}
          onOpenTask={(taskId) => {
            setCommandOpen(false);
            openTask(taskId);
          }}
          snapshot={snapshot}
        />
      ) : null}
    </div>
  );
}

function WorkspaceEmptyPanel({ onAddProject }: { onAddProject: () => void }) {
  useSettingsPreferences();
  return (
    <section className="panel project-init-panel">
      <div className="section-heading">
        <div>
          <h2>{uiText("Workspace")}</h2>
          <span className="muted">no active project</span>
        </div>
      </div>
      <p className="empty-text">
        Add or initialize a Project to open its board, agents, channels, and runtime projection.
      </p>
      <button className="primary-action" type="button" onClick={onAddProject}>
        <Plus aria-hidden="true" size={16} strokeWidth={1.8} />
        {uiText("Add Project")}
      </button>
    </section>
  );
}

function ProjectInitPanel({
  actionReady,
  onAddProject,
  onInit,
  project,
}: {
  actionReady: boolean;
  onAddProject: () => void;
  onInit: () => void;
  project: WorkspaceProject;
}) {
  useSettingsPreferences();
  const reason = projectLifecycleReason(project);
  return (
    <section className="panel project-init-panel">
      <div className="section-heading">
        <div>
          <h2>{project.name || project.project_id}</h2>
          <span className="muted">{uiText("Project needs initialization")}</span>
        </div>
      </div>
      <dl className="compact-definition-list">
        <div>
          <dt>root</dt>
          <dd>{project.root}</dd>
        </div>
        <div>
          <dt>state</dt>
          <dd>{project.state_dir_resolved || project.state_dir_hint}</dd>
        </div>
        <div>
          <dt>reason</dt>
          <dd>{reason}</dd>
        </div>
      </dl>
      <div className="action-row">
        <button className="primary-action" disabled={!actionReady} type="button" onClick={onInit}>
          {uiText("Initialize Project")}
        </button>
        <button className="icon-button" type="button" onClick={onAddProject}>
          {uiText("Open Wizard")}
        </button>
      </div>
      {!actionReady ? (
        <p className="empty-text compact-error">
          Set the Web action token before initializing this Project.
        </p>
      ) : null}
    </section>
  );
}

type ProjectFocusItem = {
  action: string;
  body: string;
  onClick?: () => void;
  tone: UiTone;
  title: string;
};

function deliveryCardDetail(taskFlowStats: TaskFlowStats, metrics: MetricsSnapshotProjection | null | undefined): string {
  const parts = [`${taskFlowStats.throughput_per_hour_24h.toFixed(1)}/h throughput`];
  if (typeof metrics?.rework_ratio === "number" && Number.isFinite(metrics.rework_ratio)) {
    parts.push(`${Math.round(metrics.rework_ratio * 100)}% rework`);
  }
  const buckets = Array.isArray(taskFlowStats.done_7d) ? taskFlowStats.done_7d : [];
  if (buckets.some((count) => count > 0)) {
    parts.push(`${sparkline(buckets)} 7d`);
  } else {
    parts.push("7d quiet");
  }
  return parts.join(" · ");
}

function ProjectHomePage({
  onOpenPage,
  projectId,
  snapshot,
  onOpenProjection,
  onOpenTask,
}: {
  onOpenPage: (page: PageId) => void;
  projectId: string;
  snapshot: Snapshot | null;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  onOpenTask: (taskId: string) => void;
}) {
  useSettingsPreferences();
  // overview-pulse.v1 is an independent project-scoped projection. A project
  // already pinned by the URL can render its pulse while workspace discovery
  // and the auxiliary light snapshot continue in parallel.
  const pulseProjectId = projectId.trim();
  const [pulseResource, setPulseResource] = useState<{
    data: OverviewPulse | null;
    projectId: string;
  }>({ data: null, projectId: "" });
  const pulse = pulseResource.projectId === pulseProjectId ? pulseResource.data : null;
  const pulseSnapshotRef = useRef<{ projectId: string; seq: number | null }>({
    projectId: "",
    seq: null,
  });
  useEffect(() => {
    if (!pulseProjectId) {
      setPulseResource({ data: null, projectId: "" });
      return;
    }
    let cancelled = false;
    fetchOverviewPulse(pulseProjectId)
      .then((next) => {
        if (!cancelled) setPulseResource({ data: next, projectId: pulseProjectId });
      })
      .catch(() => {
        if (!cancelled) setPulseResource({ data: null, projectId: pulseProjectId });
      });
    return () => {
      cancelled = true;
    };
  }, [pulseProjectId]);
  const pulseSnapshotSeq = snapshot?.project.project_id === pulseProjectId
    && typeof snapshot.seq === "number"
    ? snapshot.seq
    : null;
  useEffect(() => {
    const seen = pulseSnapshotRef.current;
    if (seen.projectId !== pulseProjectId) {
      pulseSnapshotRef.current = { projectId: pulseProjectId, seq: pulseSnapshotSeq };
      return undefined;
    }
    if (pulseSnapshotSeq === null || seen.seq === null) {
      seen.seq = pulseSnapshotSeq;
      return undefined;
    }
    if (pulseSnapshotSeq === seen.seq) return undefined;
    seen.seq = pulseSnapshotSeq;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void fetchOverviewPulse(pulseProjectId)
        .then((next) => {
          if (!cancelled) setPulseResource({ data: next, projectId: pulseProjectId });
        })
        .catch(() => undefined);
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [pulseProjectId, pulseSnapshotSeq]);
  const tasks = snapshot ? allBoardTasks(snapshot) : [];
  const activeTasks = tasks.filter((task) => task.status !== "done" && task.status !== "cancelled");
  const taskCounts = BOARD_COLUMNS.map((column) => ({
    ...column,
    count: tasks.filter((task) => taskColumn(task) === column.id).length,
  }));
  const todoCount = taskCounts.find((item) => item.id === "ready")?.count ?? 0;
  const blockedCount = taskCounts.find((item) => item.id === "blocked")?.count ?? 0;
  const doneCount = taskCounts.find((item) => item.id === "done")?.count ?? 0;
  const inProgressCount = taskCounts.find((item) => item.id === "in_progress")?.count ?? 0;
  const totalTasks = tasks.length;
  const completionRate = totalTasks ? doneCount / totalTasks : 0;
  const agents = snapshot?.agents ?? [];
  const effectiveCost = useProjectCost(projectId, snapshot?.cost);
  const fleetMetrics = buildFleetMetrics(agents, snapshot?.agent_cockpit ?? null, effectiveCost);
  const attentionRows = buildAgentAttentionRows(agents, snapshot?.agent_cockpit ?? null, snapshot?.recovery ?? null);
  const kernelMetrics = snapshot?.metrics_snapshot;
  const taskFlowStats = snapshot?.fleet_stats?.task_flow;
  // 2026-06-12: Q3 Cost 卡(方案 a,第五卡)。budget 占比待后端暴露 global_budget_usd 再显。
  const costSummary = effectiveCost;
  const costRoles = costSummary ? Object.entries(costSummary.per_role ?? {}) : [];
  const projectCost = buildProjectCostPresentation(costSummary);
  const costTokens = projectCost.totalTokens;
  const topCostRole = costRoles.slice().sort((a, b) => (b[1].usd ?? 0) - (a[1].usd ?? 0))[0];
  const fmtTok = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n));
  const allFeatures = snapshot?.features ?? [];
  const activeFeatureCount = allFeatures.filter((f) => f.status !== "done" && f.status !== "cancelled").length;
  const doneFeatureCount = allFeatures.filter((f) => f.status === "done").length;
  // Noise discipline: only warn+ rows with real evidence count as
  // attention; "fresh"/info rows are ambient state, not signals. Focus Now and
  // the Attention card share this one list so the numbers always agree.
  const actionableAttention = attentionRows.filter((row) =>
    row.severity !== "info" && String(row.reason || "").trim() !== "fresh");
  const topAttention = actionableAttention[0];
  const actionMode = snapshot?.runtime.actions?.mutation_enabled
    ? snapshot.runtime.actions.requires_token ? "token gated" : "available"
    : "read-only";
  const deliveryDetail = taskFlowStats
    ? deliveryCardDetail(taskFlowStats, kernelMetrics)
    : `${doneCount} done / ${totalTasks} total`;
  const projectCards = [
    {
      label: "Work Queue",
      value: String(activeTasks.length),
      detail: `${todoCount} todo · ${blockedCount} blocked`,
      tone: blockedCount ? "warn" : "info",
      zero: activeTasks.length === 0,
      meter: totalTasks ? activeTasks.length / totalTasks : 0,
      onClick: () => onOpenPage("board"),
    },
    {
      label: "Delivery",
      value: taskFlowStats ? `${taskFlowStats.done_24h} done · 24h` : `${Math.round(completionRate * 100)}%`,
      detail: deliveryDetail,
      tone: taskFlowStats && taskFlowStats.done_24h === 0 && inProgressCount > 0 ? "warn" : "info",
      zero: (taskFlowStats?.done_24h ?? doneCount) === 0,
      meter: completionRate,
      onClick: () => onOpenPage("delivery"),
    },
    {
      label: "Features",
      value: String(activeFeatureCount),
      detail: `${doneFeatureCount} done / ${allFeatures.length} total · ship flow`,
      tone: "info",
      zero: activeFeatureCount === 0,
      onClick: () => onOpenPage("delivery"),
    },
    {
      label: "Cost",
      value: projectCost.totalUsd > 0
        ? `$${projectCost.totalUsd.toFixed(2)}`
        : costTokens > 0 ? `${fmtTok(costTokens)} tok` : "—",
      detail: costTokens > 0
        ? `${topCostRole ? `top ${topCostRole[0]} $${(topCostRole[1].usd ?? 0).toFixed(2)} · ` : ""}${fmtTok(costTokens)} tok${projectCost.precisionLabel ? ` · ${projectCost.precisionLabel}` : ""}`
        : "no usage recorded",
      tone: projectCost.precisionTone,
      zero: !projectCost.hasUsage,
      onClick: () => onOpenPage("runtime"),
    },
    {
      label: "Attention",
      value: String(actionableAttention.length),
      detail: `${topAttention ? `${topAttention.target} · ${topAttention.reason}` : "clean"}${
        typeof pulse?.attention?.oldest_unacked_escalation_seconds === "number"
          ? ` · oldest ${formatAge(pulse.attention.oldest_unacked_escalation_seconds)}`
          : ""
      }`,
      tone: actionableAttention.length ? attentionTone(actionableAttention) : "info",
      zero: actionableAttention.length === 0,
      onClick: () => onOpenPage(topAttention?.domain === "runtime" ? "observability" : topAttention?.domain === "delivery" ? "delivery" : "agents"),
    },
  ];
  const blockedTask = tasks.find((task) => taskColumn(task) === "blocked");
  const firstActiveRun = snapshot?.active_runs[0];
  const spineReview = asRecord(snapshot?.spine_review);
  const spineStatus = textValue(spineReview.status);
  const spineVerdict = textValue(spineReview.verdict);
  const spineBetterSolution = textValue(spineReview.better_solution);
  const focusItems: ProjectFocusItem[] = [];
  for (const row of actionableAttention.slice(0, 3)) {
    focusItems.push({
      action: row.recommended_action,
      body: `${row.target}: ${row.reason}. Evidence ${row.evidence}.`,
      onClick: () => onOpenPage(row.domain === "runtime" ? "observability" : row.domain === "delivery" ? "delivery" : "agents"),
      tone: row.severity,
      title: `${row.domain} attention`,
    });
  }
  if (spineStatus === "ready" && spineVerdict && spineVerdict !== "continue") {
    focusItems.push({
      action: "Review spine insight",
      body: spineBetterSolution || `Spine review verdict: ${spineVerdict}.`,
      tone: spineVerdict === "pause_and_repair_harness" ? "warn" : "info",
      title: "Spine review",
    });
  }
  if (blockedCount) {
    focusItems.push({
      action: "Open blocked task",
      body: `${blockedCount} blocked task(s) need unblock, defer, or reassignment.`,
      onClick: blockedTask ? () => onOpenTask(blockedTask.id) : undefined,
      tone: "warn",
      title: "Blocked work",
    });
  }
  if (inProgressCount) {
    focusItems.push({
      action: "Review task flow",
      body: `${inProgressCount} task(s) are in progress; inspect Tasks for owner and evidence.`,
      tone: "info",
      title: "Work in motion",
    });
  }
  if (firstActiveRun) {
    focusItems.push({
      action: "Open active run",
      body: projectRunSummary(firstActiveRun),
      onClick: firstActiveRun.run_id ? () => onOpenProjection("run", firstActiveRun.run_id) : undefined,
      tone: "info",
      title: "Active run",
    });
  }
  if (!snapshot?.runtime.live) {
    focusItems.push({
      action: "Check Runtime",
      body: "Runtime is not live; project projections may be stale.",
      tone: "warn",
      title: "Runtime offline",
    });
  }
  if (todoCount && !blockedCount) {
    focusItems.push({
      action: "Open Tasks",
      body: `${todoCount} todo task(s) are queued for planning or dispatch.`,
      tone: "info",
      title: "Todo pressure",
    });
  }
  const projectRows = [
    { key: "repo", value: snapshot?.project.root ?? "-" },
    { key: "zf.yaml", value: snapshot ? `${snapshot.project.root}/zf.yaml` : "-" },
    { key: "state_dir", value: snapshot?.project.state_dir ?? "-" },
    { key: "seq", value: snapshot?.seq ?? 0 },
    { key: "generated_at", value: snapshot?.generated_at ?? "-" },
  ];
  const runtimeRows = [
    { key: "web_mode", value: snapshot?.runtime.web_session?.mode ?? "-" },
    { key: "web_unlocked", value: snapshot?.runtime.web_session?.unlocked ? "yes" : "no" },
    { key: "actions", value: snapshot?.runtime.actions?.mutation_enabled ? "available" : "read-only" },
    { key: "tmux_session", value: stringify(snapshot?.runtime.sessions?.tmux_session) },
    { key: "workdir_mode", value: stringify(snapshot?.runtime.workdirs?.mode) },
  ];
  const boundaryRows = [
    { key: "project_id", value: snapshot?.project.project_id ?? "-" },
    { key: "truth", value: "zf.yaml + project.state_dir" },
    { key: "events", value: "append-only" },
    { key: "api_scope", value: snapshot?.project.project_id ? `/api/projects/${snapshot.project.project_id}` : "-" },
    { key: "scope", value: "Project-local projections" },
  ];

  return (
    <section className="project-home">
      <div className="project-overview-hero">
        <div>
          <span className="badge badge-info">{uiText("Project Overview")}</span>
          <h2>{snapshot?.project.name || "Project"}</h2>
          {snapshot?.project.description ? (
            <p data-testid="project-description">{snapshot.project.description}</p>
          ) : null}
          <p className="muted">
            snapshot #{snapshot?.seq ?? 0} · {formatTime(snapshot?.generated_at ?? "") || "not generated"}
          </p>
        </div>
        <div className="project-hero-badges">
          <span className={`badge badge-${snapshot?.runtime.live ? "ok" : "warn"}`}>
            {snapshot?.runtime.live ? "live" : "offline"}
          </span>
          <span className={`badge badge-${snapshot?.runtime.actions?.mutation_enabled ? "warn" : "info"}`}>
            {actionMode}
          </span>
          <span className="badge badge-info">projection only</span>
        </div>
      </div>
      <div className="project-health-grid">
        {projectCards.map((card) => {
          const content = (
            <>
              <span>{card.label}</span>
              <strong>{card.value}</strong>
              <p>{card.detail}</p>
              {typeof card.meter === "number" ? (
                <div className="project-health-meter">
                  <span style={{ width: `${Math.max(0, Math.min(100, card.meter * 100))}%` }} />
                </div>
              ) : null}
            </>
          );
          const cardClass = card.zero
            ? "project-health-card is-zero"
            : `project-health-card tone-${card.tone}`;
          return card.onClick ? (
            <button className={cardClass} key={card.label} type="button" onClick={card.onClick}>
              {content}
            </button>
          ) : (
            <article className={cardClass} key={card.label}>
              {content}
            </article>
          );
        })}
      </div>
      <PulseBand pulse={pulse?.run_pulse ?? null} />
      <ProjectFocusPanel items={focusItems} />
      <TaskFlowBand
        fallbackCounts={taskCounts}
        fallbackFlowStats={taskFlowStats ?? null}
        flow={pulse?.task_flow ?? null}
        onOpenPage={onOpenPage}
        onOpenTask={onOpenTask}
        tasks={tasks}
        whyNot={pulse?.why_not ?? null}
      />
      {/* doc116 §7.1 deletions (operator-approved 2026-07-02): VQE metrics ->
          Loop/Graph pages; Control Plane trio -> Settings; Runs -> Observability.
          Spine Review is event-driven: it only appears with a real verdict. */}
      {Boolean(spineStatus === "ready" && spineVerdict && spineVerdict !== "continue") && (
        <SpineReviewInsightCard insight={spineReview} />
      )}
    </section>
  );
}

function SpineReviewInsightCard({ insight }: { insight: Record<string, unknown> }) {
  useSettingsPreferences();
  const status = textValue(insight.status);
  const verdict = textValue(insight.verdict);
  const confidence = textValue(insight.confidence);
  const actions = asRecordArray(insight.corrective_actions).slice(0, 3);
  const findings = asStringArray(insight.top_findings).slice(0, 4);
  const ready = status === "ready";
  const tone = !ready
    ? "info"
    : verdict === "continue"
      ? "ok"
      : verdict === "pause_and_repair_harness"
        ? "warn"
        : "info";
  return (
    <section className={`spine-review-card tone-${tone}`}>
      <div className="inline-heading">
        <div>
          <h3 className="section-title">Spine Review</h3>
          <p className="muted">Design · Delivery · Runtime · Reflection</p>
        </div>
        <span className={`badge badge-${tone}`}>{ready ? verdict || "reviewed" : "not reviewed"}</span>
      </div>
      {ready ? (
        <>
          <div className="spine-review-status-grid">
            <span>Design <strong>{textValue(insight.design_status) || "-"}</strong></span>
            <span>{uiText("Delivery")} <strong>{textValue(insight.delivery_status) || "-"}</strong></span>
            <span>{uiText("Runtime")} <strong>{textValue(insight.runtime_status) || "-"}</strong></span>
            <span>Confidence <strong>{confidence || "-"}</strong></span>
          </div>
          <p className="spine-review-solution">
            {textValue(insight.better_solution) || "No corrective action recommended."}
          </p>
          {findings.length ? (
            <div className="spine-review-findings">
              {findings.map((finding) => <span key={finding}>{finding}</span>)}
            </div>
          ) : null}
          {actions.length ? (
            <div className="spine-review-actions">
              {actions.map((action) => (
                <article key={textValue(action.action_id) || textValue(action.target)}>
                  <span className="badge badge-info">{textValue(action.priority) || "P?"}</span>
                  <strong>{textValue(action.kind) || "action"}</strong>
                  <p>{textValue(action.target) || textValue(action.proposal)}</p>
                </article>
              ))}
            </div>
          ) : null}
          <p className="muted mono">
            review {textValue(insight.review_id) || "-"} · {formatTime(textValue(insight.last_reviewed_at)) || "-"}
          </p>
        </>
      ) : (
        <p className="project-empty-state">{uiText("No spine review artifact yet.")}</p>
      )}
    </section>
  );
}

function ProjectFocusPanel({ items }: { items: ProjectFocusItem[] }) {
  useSettingsPreferences();
  const visibleItems = items.slice(0, 3);
  const hiddenCount = Math.max(0, items.length - visibleItems.length);
  return (
    <section className="project-focus-section">
      <div className="inline-heading">
        <h3 className="section-title">{uiText("Focus Now")}</h3>
        <span className="muted">{visibleItems.length}/{items.length} signal(s)</span>
      </div>
      {items.length === 0 ? (
        <p className="project-empty-state">{uiText("No project-level attention signal.")}</p>
      ) : (
        <div className="compact-list">
          {visibleItems.map((item) => {
            const content = (
              <>
                <span className={`badge badge-${item.tone}`}>{item.tone}</span>
                <strong>{item.title}</strong>
                <span className="project-focus-body">{item.body}</span>
                <span className="muted">{item.action}</span>
              </>
            );
            return item.onClick ? (
              <button className="inline-row" key={item.title} type="button" onClick={item.onClick}>
                {content}
              </button>
            ) : (
              <div className="inline-row" key={item.title}>
                {content}
              </div>
            );
          })}
          {hiddenCount ? (
            <div className="inline-row project-focus-more">
              <span className="badge badge-info">+{hiddenCount}</span>
              <strong>{uiText("More signals")}</strong>
              <span className="project-focus-body">{uiText("Open Agents, Delivery, or Observability for the full attention queue.")}</span>
              <span className="muted">triage later</span>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function ProjectRunsOverview({
  activeRuns,
  onOpenProjection,
  recentRuns,
}: {
  activeRuns: RunSummary[];
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  recentRuns: RunSummary[];
}) {
  useSettingsPreferences();
  const [runsTab, setRunsTab] = useState<"active" | "recent">(activeRuns.length ? "active" : "recent");
  const showActive = runsTab === "active";
  return (
    <div className="project-runs-overview">
      <div className="inline-heading">
        <h3 className="section-title">{uiText("Runs")}</h3>
        <span className="tab-strip">
          <button
            className={`tab-chip${showActive ? " active" : ""}`}
            type="button"
            onClick={() => setRunsTab("active")}
          >
            {uiText("Active")} {activeRuns.length}
          </button>
          <button
            className={`tab-chip${showActive ? "" : " active"}`}
            type="button"
            onClick={() => setRunsTab("recent")}
          >
            {uiText("Recent")} {recentRuns.length}
          </button>
        </span>
      </div>
      <ProjectRunColumn
        emptyText={showActive ? "No active runs right now." : "No recent run projection."}
        onOpenProjection={onOpenProjection}
        runs={showActive ? activeRuns : recentRuns}
      />
    </div>
  );
}

function ProjectRunColumn({
  emptyText,
  onOpenProjection,
  runs,
}: {
  emptyText: string;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  runs: RunSummary[];
}) {
  return (
    <section className="subsection project-run-column">
      {runs.length === 0 ? (
        <div className="project-empty-state">{emptyText}</div>
      ) : (
        <div className="project-run-list">
          {runs.map((run, index) => (
            <button
              className="project-run-card"
              key={run.run_id || index}
              type="button"
              onClick={() => {
                if (run.run_id) onOpenProjection("run", run.run_id);
              }}
            >
              <div className="project-run-head">
                <span className={`badge badge-${automationStatusTone(run.status || run.health || "")}`}>
                  {run.status || run.health || "unknown"}
                </span>
                <strong>{automationShortRunId(run.run_id || "run")}</strong>
              </div>
              <p>{projectRunSummary(run)}</p>
              <div className="project-run-meta">
                {run.started_at ? <span>{formatTime(run.started_at)}</span> : null}
                {run.ended_at ? <span>ended {formatTime(run.ended_at)}</span> : null}
                {run.health ? <span>{run.health}</span> : null}
              </div>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function projectRunSummary(run: RunSummary): string {
  const summary = asRecord(run.summary);
  const eventCount = summary.event_count ?? summary.task_event_count;
  const taskCount = summary.task_count ?? (Array.isArray(summary.task_ids) ? summary.task_ids.length : undefined);
  const parts = [
    run.scenario_id ? String(run.scenario_id) : "",
    eventCount !== undefined ? `${stringify(eventCount)} events` : "",
    taskCount !== undefined ? `${stringify(taskCount)} tasks` : "",
  ].filter(Boolean);
  return parts.join(" · ") || run.live_state_dir || run.trace_id || "Run projection is available.";
}

// P5/W4 (docs/impl/22-zaofu-canonical-dag.md §4.3): red banner shown in the
// task detail header when the kernel has emitted task.contract.invalid for
// this task and no subsequent task.contract.update has resolved it.
//
// task.contract.invalid is emitted by:
//   - P2/K4 dispatch preflight (missing required_backlog_refs)
//   - Layer 1 dispatcher validate_task_contract failures (any contract gap)
//
// Banner reads the events stream + detail.events to find the most recent
// invalid; clears when a later task.contract.update or task.dispatched fires.
function TriagePage({
  actionResult,
  events,
  pendingProposals,
  onAction,
  onOpenTask,
  tasks,
}: {
  actionResult: ActionResponse | null;
  events: RecentEvent[];
  pendingProposals: PendingKanbanProposal[];
  onAction: (action: string, payload: Record<string, unknown>) => void;
  onOpenTask: (taskId: string) => void;
  tasks: Task[];
}) {
  useSettingsPreferences();
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const failedEvents = events.filter((event) => (
    event.type.includes("failed")
    || event.type.includes("rejected")
    || event.type === "web.action.failed"
    || event.type === "runtime.action.failed"
  )).slice(0, 12);
  const autopilotEvents = events
    .filter((event) => (
      event.type === "autopilot.proposal.created"
      // Feishu-surface kanban agent proposals have no Web chat panel to
      // render their Accept in; the proposal-only triage queue is their
      // approval entry point.
      || event.type === "kanban.agent.action.proposed"
      || (
        event.type === "operator.action.proposed"
        && Boolean(asRecord(event.payload).proposal)
      )
    ))
    .slice(0, 12);
  const blocked = tasks.filter((task) => task.status === "blocked" || Boolean(task.blocked_reason));
  const stale = tasks
    .filter((task) => task.status !== "done" && task.status !== "cancelled" && latestEventAge(task).endsWith("d"))
    .slice(0, 12);
  const isVisible = (id: string) => !dismissed.has(id);
  const dismiss = (id: string) => setDismissed((current) => {
    const next = new Set(current);
    next.add(id);
    return next;
  });

  const buildAutopilotItem = (opts: {
    proposalId: string;
    action: string;
    valid: boolean;
    actionPayload: Record<string, unknown> | null;
    title: string;
    metaKind: string;
    metaSeverity: string;
    taskId: string;
  }) => {
    const dismissId = `autopilot:${opts.proposalId}`;
    const actions: Array<{ label: string; run: () => void }> = [];
    if (opts.valid && opts.action && opts.actionPayload) {
      actions.push({
        label: "Accept",
        run: () => onAction(opts.action, {
          ...opts.actionPayload,
          proposal_id: opts.proposalId,
          source: "autopilot-proposal",
        }),
      });
    }
    if (opts.taskId) actions.push({ label: "Edit", run: () => onOpenTask(opts.taskId) });
    actions.push({ label: "Dismiss", run: () => dismiss(dismissId) });
    return {
      id: dismissId,
      title: opts.title || opts.action,
      meta: `${opts.metaKind} · ${opts.metaSeverity}`,
      hidden: !isVisible(dismissId),
      actions,
    };
  };

  // Durable pending-proposals projection is the source of truth (survives the
  // event window and browser session); live autopilot events only add
  // freshly-arrived / non-kanban (autopilot.proposal.created) proposals not yet
  // reflected in the durable list. See ./triageProposals.
  const liveAutopilotDescriptors: AutopilotProposalDescriptor[] = autopilotEvents.map((event) => {
    const payload = recordValue(event.payload) ?? {};
    const proposal = headlessActionProposal(payload);
    const proposalTitle = proposal ? textValue(proposal.payload.title || proposal.action) : "";
    return {
      proposalId: textValue(payload.proposal_id || event.id || event.seq),
      action: proposal?.action ?? "",
      valid: Boolean(proposal?.valid),
      actionPayload: proposal?.payload ?? null,
      title: textValue(payload.title) || proposalTitle || event.type,
      metaKind: textValue(payload.kind || payload.source || "proposal"),
      metaSeverity: textValue(payload.severity || "medium"),
      taskId: textValue(payload.task_id || event.task_id),
    };
  });
  const autopilotItems = mergeAutopilotDescriptors(pendingProposals, liveAutopilotDescriptors)
    .map(buildAutopilotItem);

  return (
    <section className="triage-page">
      <div className="section-heading">
        <div>
          <h2>{uiText("Triage")}</h2>
          <span className="muted">proposal-only queue</span>
        </div>
        <div className="button-row">
          {dismissed.size ? (
            <button className="icon-button" type="button" onClick={() => setDismissed(new Set())}>
              {uiText("Restore")} {dismissed.size}
            </button>
          ) : null}
          {actionResult ? <span className="badge">{actionResult.status}</span> : null}
        </div>
      </div>
      <div className="triage-grid">
        <TriageList
          title="Autopilot"
          empty="No autopilot proposals."
          items={autopilotItems}
        />
        <TriageList
          title={uiText("Blocked")}
          empty="No blocked tasks."
          items={blocked.map((task) => ({
            id: `blocked:${task.id}`,
            title: task.title,
            meta: task.blocked_reason || task.status,
            hidden: !isVisible(`blocked:${task.id}`),
            actions: [
              { label: "Accept", run: () => onAction("update-task", { task_id: task.id, status: "blocked", blocked_reason: task.blocked_reason || "triage confirmed blocked" }) },
              { label: "Edit", run: () => onOpenTask(task.id) },
              { label: "Dismiss", run: () => dismiss(`blocked:${task.id}`) },
            ],
          }))}
        />
        <TriageList
          title="Failed / Rejected"
          empty="No failed events."
          items={failedEvents.map((event) => ({
            id: `failed:${event.id ?? event.seq}`,
            title: event.type,
            meta: `${event.task_id ?? "project"} · ${formatTime(event.ts)}`,
            hidden: !isVisible(`failed:${event.id ?? event.seq}`),
            actions: event.task_id ? [
              { label: "Accept", run: () => onAction("request-review", { task_id: event.task_id }) },
              { label: "Edit", run: () => onOpenTask(String(event.task_id)) },
              { label: "Dismiss", run: () => dismiss(`failed:${event.id ?? event.seq}`) },
            ] : [],
          }))}
        />
        <TriageList
          title="Stale"
          empty="No stale tasks."
          items={stale.map((task) => ({
            id: `stale:${task.id}`,
            title: task.title,
            meta: `latest ${latestEventAge(task)}`,
            hidden: !isVisible(`stale:${task.id}`),
            actions: [
              { label: "Accept", run: () => onAction("update-task", { task_id: task.id, status: "blocked", blocked_reason: `triage stale scan: latest ${latestEventAge(task)}` }) },
              { label: "Edit", run: () => onOpenTask(task.id) },
              { label: "Dismiss", run: () => dismiss(`stale:${task.id}`) },
            ],
          }))}
        />
      </div>
    </section>
  );
}

function TriageList({
  empty,
  items,
  title,
}: {
  empty: string;
  items: Array<{ id: string; title: string; meta: string; hidden?: boolean; actions: Array<{ label: string; run: () => void }> }>;
  title: string;
}) {
  const visibleItems = items.filter((item) => !item.hidden);
  return (
    <div className="subsection triage-list">
      <h3>{title}</h3>
      {visibleItems.length ? visibleItems.map((item) => (
        <article className="triage-item" key={item.id}>
          <div>
            <strong>{item.title}</strong>
            <span className="muted">{item.meta}</span>
          </div>
          <div className="button-row">
            {item.actions.map((action) => (
              <button className="icon-button" key={action.label} type="button" onClick={action.run}>
                {action.label}
              </button>
            ))}
          </div>
        </article>
      )) : <p className="empty-text compact-error">{empty}</p>}
    </div>
  );
}

function CommandPalette({
  actionReady,
  canViewIssueTriage,
  events,
  onAction,
  onClose,
  onOpenAgent,
  onOpenPage,
  onOpenProjection,
  onOpenTask,
  snapshot,
}: {
  actionReady: boolean;
  canViewIssueTriage: boolean;
  events: RecentEvent[];
  onAction: (action: string, payload: Record<string, unknown>) => void;
  onClose: () => void;
  onOpenAgent: () => void;
  onOpenPage: (page: PageId) => void;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  onOpenTask: (taskId: string) => void;
  snapshot: Snapshot | null;
}) {
  useSettingsPreferences();
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const tasks = (snapshot ? allBoardTasks(snapshot) : [])
    .filter((task) => {
      if (!needle) return true;
      return `${task.id} ${task.title} ${task.status} ${task.assigned_to}`.toLowerCase().includes(needle);
    })
    .slice(0, 8);
  const runRows = [
    ...(snapshot?.active_runs ?? []),
    ...(snapshot?.runs ?? []),
  ].filter((row) => !needle || stringify(row).toLowerCase().includes(needle)).slice(0, 5);
  const fanoutRows = (snapshot?.fanouts ?? [])
    .filter((row) => !needle || stringify(row).toLowerCase().includes(needle))
    .slice(0, 5);
  const eventRows = events
    .filter((event) => !needle || stringify(event).toLowerCase().includes(needle))
    .slice(0, 5);

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel command-palette" role="dialog" aria-modal="true" aria-label={uiText("Command Palette")}>
        <div className="section-heading">
          <div>
            <h2>{uiText("Command Palette")}</h2>
            <span className="muted">workspace actions and search</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose}>{uiText("Close")}</button>
        </div>
        <div className="modal-body">
          <input
            autoFocus
            className="filter-input command-input"
            placeholder="task, event, run, fanout"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="command-actions">
            <button className="icon-button" type="button" onClick={onOpenAgent}>Kanban Agent</button>
            {canViewIssueTriage ? <button className="icon-button" type="button" onClick={() => onOpenPage("issue-triage")}>{uiText("Triage")}</button> : null}
            <button className="icon-button" type="button" onClick={() => onOpenPage("board")}>{uiText("Board")}</button>
          </div>
          <div className="command-grid">
            <div className="subsection command-section">
              <h3>{uiText("Tasks")}</h3>
              {tasks.length ? tasks.map((task) => (
                <div className="command-item" key={task.id}>
                  <button className="link-button" type="button" onClick={() => onOpenTask(task.id)}>
                    <span className="mono">{task.id}</span> {task.title || "-"}
                  </button>
                  <div className="button-row">
                    <span className="badge">P{taskPriority(task)}</span>
                    <button className="icon-button" type="button" onClick={() => onOpenTask(task.id)}>{uiText("Open")}</button>
                    <button
                      className="icon-button"
                      disabled={!actionReady}
                      type="button"
                      onClick={() => onAction("update-task", { task_id: task.id, status: "blocked", blocked_reason: "blocked from command palette" })}
                    >
                      Block
                    </button>
                    <button
                      className="icon-button"
                      disabled={!actionReady}
                      type="button"
                      onClick={() => onAction("request-review", { task_id: task.id })}
                    >
                      {uiText("Review")}
                    </button>
                  </div>
                </div>
              )) : <p className="empty-text compact-error">{uiText("No tasks.")}</p>}
            </div>
            <div className="subsection command-section">
              <h3>{uiText("Execution")}</h3>
              {runRows.map((row) => (
                <button className="inline-row" key={`run:${textValue(row.run_id)}`} type="button" onClick={() => onOpenProjection("run", textValue(row.run_id))}>
                  <span>run</span>
                  <span className="mono">{textValue(row.run_id)}</span>
                  <span>{textValue(row.status || row.health)}</span>
                </button>
              ))}
              {fanoutRows.map((row) => (
                <button className="inline-row" key={`fanout:${textValue(row.fanout_id)}`} type="button" onClick={() => onOpenProjection("fanout", textValue(row.fanout_id))}>
                  <span>fanout</span>
                  <span className="mono">{textValue(row.fanout_id)}</span>
                  <span>{textValue(row.status)}</span>
                </button>
              ))}
              {!runRows.length && !fanoutRows.length ? <p className="empty-text compact-error">{uiText("No execution rows.")}</p> : null}
            </div>
            <div className="subsection command-section">
              <h3>{uiText("Events")}</h3>
              {eventRows.length ? eventRows.map((event) => (
                <button className="inline-row" key={String(event.id ?? event.seq)} type="button" onClick={() => onOpenPage("events")}>
                  <span className="mono">{event.seq ?? "-"}</span>
                  <span>{event.type}</span>
                  <span>{formatTime(event.ts)}</span>
                </button>
              )) : <p className="empty-text compact-error">{uiText("No events.")}</p>}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function NewChannelModal({
  actionReady,
  draft,
  onClose,
  onDraftChange,
  onSubmit,
}: {
  actionReady: boolean;
  draft: NewChannelDraft;
  onClose: () => void;
  onDraftChange: (draft: NewChannelDraft) => void;
  onSubmit: () => void;
}) {
  useSettingsPreferences();
  const update = (patch: Partial<NewChannelDraft>) => onDraftChange({ ...draft, ...patch });
  const name = draft.name.trim();
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel" role="dialog" aria-modal="true" aria-label={uiText("New Channel")}>
        <div className="section-heading">
          <div>
            <h2>{uiText("New Channel")}</h2>
            <span className="muted">event-backed channel; no side state file</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose}>{uiText("Close")}</button>
        </div>
        <div className="modal-body">
          <input
            autoFocus
            className="filter-input"
            placeholder="# channel name"
            value={draft.name}
            onChange={(event) => update({ name: event.target.value })}
          />
          <input
            className="filter-input"
            placeholder="optional channel id, e.g. research"
            value={draft.channelId}
            onChange={(event) => update({ channelId: event.target.value })}
          />
        </div>
        <div className="action-row">
          <button className="icon-button primary" disabled={!actionReady || !name || name === "#"} type="button" onClick={onSubmit}>
            {uiText("Create Channel")}
          </button>
          <button className="icon-button" type="button" onClick={() => onDraftChange(emptyNewChannelDraft())}>
            {uiText("Reset")}
          </button>
        </div>
      </section>
    </div>
  );
}

function SearchResults({ result, onSelectTask }: { result: SearchResult; onSelectTask: (taskId: string) => void }) {
  useSettingsPreferences();
  return (
    <div className="subsection">
      <h3>{uiText("Search")}</h3>
      <div className="compact-list">
        {result.tasks.map((task) => (
          <button className="inline-row" type="button" key={task.id} onClick={() => onSelectTask(task.id)}>
            <span className="mono">{task.id}</span>
            <span>{task.title}</span>
            <span className="muted">{task.status}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
