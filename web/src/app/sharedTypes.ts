// Shared types extracted verbatim from App.tsx (P1 frontend split).
import type { LucideIcon } from "lucide-react";

export const NAV_PAGES = [
  "inbox",
  "channels",
  "board",
  "triage",
  "observability",
  "events",
  "agents",
  "automations",
  "backlogs",
  "workdirs",
  "skills",
  "traces",
  "delivery",
  "goal-coverage",
  "control-room",
  "delivery-trace",
  "delivery-graph",
  "behavior-loop",
  "diagnostics",
  "candidates",
  "fanouts",
  "runs",
  "archives",
  "runtime",
  "settings",
] as const;
export const PAGES = ["project", ...NAV_PAGES, "task"] as const;
export const DETAIL_TABS = [
  "Summary",
  "Activity",
  "Evidence",
  "Advanced",
] as const;
export const OPERATOR_BACKENDS = [
  { id: "claude-headless", title: "Claude" },
  { id: "codex-headless", title: "Codex" },
  { id: "deterministic", title: "deterministic" },
  { id: "codex", title: "codex terminal" },
  { id: "claude-code", title: "claude-code terminal" },
] as const;

export type OperatorBackend = (typeof OPERATOR_BACKENDS)[number]["id"];


export interface AddAgentDraft {
  memberId: string;
  memberType: ChannelMemberType;
  provider: string;
  providerBindingId: string;
  channelRole: ChannelRole;
  visibilityProfile: VisibilityProfile;
  roleContextRef: string;
  skillRefs: string;
  backend: string;
  scope: string;
  reason: string;
  permissionProfile: ChannelPermissionProfile;
  dangerousAck: boolean;
  canMessage: boolean;
  canSummarize: boolean;
  canProposeWorkflow: boolean;
}


export type AgentPanelMode = "collapsed" | "docked" | "fullscreen";

export type ChannelMemberType =
  | "human"
  | "provider_agent"
  | "persona_agent"
  | "owner_delegate"
  | "runtime_role_binding"
  | "observer"
  | "automation_reporter";

export type ChannelPermissionProfile = "read_only" | "artifact_writer" | "project_writer" | "dangerous_full";

export type ChannelRole =
  | "arch"
  | "facilitator"
  | "tech_leader"
  | "product_pm"
  | "researcher"
  | "synthesizer"
  | "security_reviewer"
  | "qa_analyst"
  | "dev_reviewer"
  | "critic"
  | "spine_reviewer"
  | "owner_delegate"
  | "automation_reporter"
  | "observer";

export type DetailTab = (typeof DETAIL_TABS)[number];

export interface EmptyStateAction {
  label: string;
  onClick?: () => void;
}


export interface EmptyStateSpec {
  title: string;
  description: string;
  icon?: LucideIcon;
  actions?: EmptyStateAction[];
  compact?: boolean;
}


export type LiveState = "connecting" | "live" | "reconnecting" | "degraded";

export type NewTaskAssigneeType = "none" | "agent" | "squad";



export interface OrchestratorContext {
  taskId: string;
  traceId: string;
  pddId: string;
  fanoutId: string;
  stageId: string;
  targetRef: string;
  title: string;
}


export type PageId = (typeof PAGES)[number];

export type ParsedEventFilter = {
  actor?: string;
  blocked?: boolean;
  failed?: boolean;
  prefix?: string;
  task?: string;
  type?: string;
  unknown: string[];
};


export type ProjectionKind = "trace" | "candidate" | "fanout" | "run";

export interface ProjectionMetricSpec {
  icon?: LucideIcon;
  label: string;
  meta: string;
  tone?: UiTone;
  value: number | string;
}


export type ThemeMode = "system" | "dark" | "light";

export type UiTone = "ok" | "warn" | "err" | "info" | "muted";

export type ViewMode = "board" | "list";

export type VisibilityProfile = "minimal" | "planner" | "reviewer" | "owner_report" | "full_audit";
