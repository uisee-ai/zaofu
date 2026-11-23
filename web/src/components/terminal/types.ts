export type TerminalProvider = "claude-code" | "codex" | "opencode" | "pi";
export type TerminalAttachmentMode = "observe" | "control";

export interface TerminalCapability {
  available: boolean;
  backend: string;
  binary: string;
  version: string;
  schema_available: boolean;
  observe_bridge: boolean;
  control_bridge: boolean;
  tab_rename: boolean;
  mouse?: boolean;
  reason: string;
}

export interface TerminalUsage {
  schema_version: "terminal-usage.v1";
  status: "observed" | "awaiting_usage" | "unavailable" | "unsupported" | string;
  source: "provider_transcript" | "terminal_cost_ledger" | string;
  provider: string;
  accounting_mode: string;
  model: string;
  models: string[];
  fresh_input_tokens: number | null;
  cached_input_tokens: number | null;
  cache_creation_input_tokens: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  reasoning_output_tokens: number | null;
  total_tokens: number | null;
  cost_usd: number | null;
  cost_kind: "estimated" | "partial_estimate" | "unpriced" | "unavailable" | string;
  context_usage_ratio: number | null;
  observed_at: string;
  reason: string;
}

export interface TerminalSession {
  session_id: string;
  slot: string;
  title: string;
  provider: TerminalProvider;
  provider_kind: string;
  project_id: string;
  state: "active" | "stopped" | "missing" | string;
  generation: number;
  created_at: string;
  updated_at: string;
  stopped_at?: string;
  diagnostics: string[];
  agent_status?: "working" | "blocked" | "idle" | "done" | "";
  usage?: TerminalUsage;
}

export interface TerminalSessionsPage {
  schema_version: "terminal-sessions.v1";
  enabled: boolean;
  backend: string;
  allowed_providers: TerminalProvider[];
  allow_takeover: boolean;
  capability: TerminalCapability;
  sessions: TerminalSession[];
}

export interface TerminalMutationResponse {
  ok: boolean;
  status?: string;
  reason?: string;
  session?: TerminalSession;
}

export interface TerminalAttachmentTicket {
  ok: true;
  schema_version: "terminal-attachment-ticket.v1";
  ticket: string;
  subprotocol: "zf-terminal-v1";
  mode: TerminalAttachmentMode;
  expires_in_seconds: number;
}

export interface TerminalFrame {
  type: "terminal.frame";
  seq: number;
  encoding: "ansi";
  width: number;
  height: number;
  full: boolean;
}

export interface TerminalClosed {
  type: "terminal.closed";
  reason: string;
}

export type TerminalServerMessage = TerminalFrame | TerminalClosed;
