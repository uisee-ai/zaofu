import type { EventRecord } from "../../api/types";

export type AgentSessionSurface = "kanban_agent" | "channel_group";
export type AgentSessionStatus =
  | "idle"
  | "submitted"
  | "streaming"
  | "waiting_input"
  | "queued"
  | "completed"
  | "failed"
  | "cancelled"
  | "stale";

export type AgentPartKind =
  | "text"
  | "thinking"
  | "tool"
  | "tool_call"
  | "tool_result"
  | "command"
  | "file_read"
  | "file_change"
  | "code_preview"
  | "diff_preview"
  | "test_result"
  | "artifact_preview"
  | "trace_ref"
  | "action_proposal"
  | "approval_request"
  | "context_ledger"
  | "status"
  | "question"
  | "proposal"
  | "error";

export interface AgentSessionPart {
  id: string;
  runId: string;
  kind: AgentPartKind;
  state: AgentSessionStatus;
  title: string;
  summary?: string;
  content?: string;
  contentRef?: string;
  seq?: number;
  toolCallId?: string;
  toolName?: string;
  startedAt?: string;
  updatedAt?: string;
  sourceEventId?: string;
  sourceEventSeq?: number;
  sourceEvent?: EventRecord;
  refs?: Record<string, unknown>;
}

export interface AgentSessionActionProposal {
  action: string;
  requestedAction: string;
  payload: Record<string, unknown>;
  reason: string;
  confidence?: string;
  valid: boolean;
  validationError?: string;
}

export interface AgentSessionCard {
  id: string;
  kind: "question" | "proposal" | "queue" | "run-status" | "capability" | "context-ledger" | "preview";
  title: string;
  body?: string;
  status?: AgentSessionStatus;
  runId?: string;
  threadId?: string;
  actionLabel?: string;
  proposal?: AgentSessionActionProposal;
  payload?: Record<string, unknown>;
  refs?: Record<string, unknown>;
}

export interface AgentProviderCapability {
  provider: string;
  streaming?: boolean;
  cancel?: boolean;
  resume?: boolean;
  native_resume?: boolean;
  interrupt?: boolean;
  tools?: boolean;
  cost?: boolean;
  context_usage?: boolean;
  context?: string;
  workdir?: string;
  test_mode?: boolean;
  source?: string;
  available?: boolean;
}

export interface AgentSessionRun {
  id: string;
  threadId: string;
  provider?: string;
  memberId?: string;
  role?: string;
  status: AgentSessionStatus;
  startedAt?: string;
  updatedAt?: string;
  providerSessionId?: string;
  parts: AgentSessionPart[];
  proposal?: AgentSessionActionProposal;
  usage?: Record<string, unknown>;
  stale?: boolean;
  sourceEvents?: EventRecord[];
}

export interface AgentSessionMessage {
  id: string;
  role: "user" | "assistant" | "system";
  label: string;
  content: string;
  ts?: string;
  memberId?: string;
  provider?: string;
  sourceEvent?: EventRecord;
  refs?: Record<string, unknown>;
  // feishu-C #1: external origin (Feishu/OpenClaw) for the source chip.
  origin?: { channel: string; chat_id: string };
}

export interface AgentSessionTurn {
  id: string;
  threadId: string;
  user?: AgentSessionMessage;
  runs: AgentSessionRun[];
  cards: AgentSessionCard[];
  ts?: string;
}

export interface AgentSessionThread {
  id: string;
  title: string;
  status: AgentSessionStatus;
  turns: AgentSessionTurn[];
  activeRunId?: string;
  provider?: string;
  participantRefs?: string[];
  unseenCount?: number;
  updatedAt?: string;
}

export interface AgentConversation {
  id: string;
  projectId?: string;
  surface: AgentSessionSurface;
  activeThreadId: string;
  threads: AgentSessionThread[];
}

export interface AgentSessionThreadRef {
  id: string;
  title: string;
  createdAt?: string;
}
