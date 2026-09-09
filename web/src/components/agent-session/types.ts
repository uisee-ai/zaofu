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
  proposalEventId?: string;
  proposalId?: string;
  proposalDigest?: string;
  revision?: number;
  action: string;
  requestedAction: string;
  payload: Record<string, unknown>;
  reason: string;
  confidence?: string;
  valid: boolean;
  validationError?: string;
}

export interface AgentSessionPlanOption {
  id: string;
  label: string;
  description?: string;
  recommended?: boolean;
  submitAction?: string;
  submitMode?: "apply" | "continue" | "propose";
  submitDetails?: {
    templateId?: string;
    templateName?: string;
    taskId?: string;
    objective?: string;
    memberCount?: number;
    roles?: string[];
    maxRounds?: number;
    roundPolicy?: string;
    profiles?: Array<{
      memberId?: string;
      channelRole?: string;
      profileId?: string;
      displayName?: string;
    }>;
    mode?: string;
    engineMode?: string;
    routingStrategy?: string;
    firstPassReplyCount?: number;
    routeId?: string;
    family?: string;
    kind?: string;
    tier?: string;
    topology?: string;
    writerRoles?: string[];
    verifyRoles?: string[];
    laneCount?: number;
    outputProfile?: string;
    inputKeys?: string[];
    preflight?: string;
  };
}

export interface AgentSessionPlanQuestion {
  id: string;
  header: string;
  question: string;
  options: AgentSessionPlanOption[];
  allowOther: boolean;
}

export interface AgentSessionPlanAnswer {
  questionId: string;
  optionId: string;
  answer: string;
}

export interface AgentSessionPlanResponse {
  requestEventId: string;
  requestId: string;
  revision: number;
  questionId: string;
  optionId: string;
  answer: string;
  answers: AgentSessionPlanAnswer[];
  answerEventId?: string;
}

export interface AgentSessionPlanRequest {
  requestEventId: string;
  requestId: string;
  requestDigest?: string;
  revision: number;
  header: string;
  subjectType?: "channel_setup" | "clarification" | "task_create" | "task_workflow";
  questionId: string;
  question: string;
  options: AgentSessionPlanOption[];
  allowOther: boolean;
  questions: AgentSessionPlanQuestion[];
  reason?: string;
  valid: boolean;
  validationError?: string;
  response?: AgentSessionPlanResponse;
  backend?: string;
  providerSessionId?: string;
  submitAction?: string;
  submitMode?: "apply" | "continue" | "propose";
  submitLabel?: string;
  configDigest?: string;
}

export interface AgentSessionCard {
  id: string;
  kind: "plan" | "approve" | "question" | "proposal" | "queue" | "run-status" | "capability" | "context-ledger" | "preview" | "workflow-result" | "contribution";
  title: string;
  body?: string;
  status?: AgentSessionStatus;
  runId?: string;
  threadId?: string;
  actionLabel?: string;
  proposal?: AgentSessionActionProposal;
  planRequest?: AgentSessionPlanRequest;
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
