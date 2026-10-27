import { cachedGetJson } from "./queryClient";

export interface ChannelDeliveryEvidence {
  revision: number;
  artifact_digest: string;
  plans: Array<{ request_id: string; subject_type: string; task_id: string; status: string }>;
  runs: Array<{ task_id: string; workflow_run_id: string; status: string; event_id: string; reason: string }>;
  planning: { status?: string; event_id?: string; task_id?: string };
}
export interface ChannelPrdDocument {
  channel_id: string;
  thread_id: string;
  revision: number;
  artifact_ref: string;
  artifact_digest: string;
  title: string;
  markdown: string;
  byte_count: number;
  sources?: { status: "ready" | "incomplete" | "unavailable"; items: Array<{
    kind: string; message_id: string; digest: string; name: string; content: string; available: boolean;
  }> };
  requirement_coverage?: Array<Record<string, unknown>>;
  readiness?: { ref: string; digest: string; verdict: string; implementation_start: boolean;
    reason: string; gaps: unknown[]; risks: unknown[] } | null;
}
export interface ChannelPrdSelection {
  projectId: string; channelId: string; threadId: string; revision: number; digest: string;
}
export interface ChannelPrdEvaluationView {
  revision: number;
  artifact_digest: string;
  evaluations: Array<{ role: "full" | "focused"; status: string; verdict: string;
    request_id: string; reason?: string; report_ref?: string; report_digest?: string;
    feedback?: { status: string; reason?: string; review?: { ref: string; sha256: string } };
    findings: Array<Record<string, unknown>>; coverage?: unknown; score?: unknown }>;
}
export function channelDocumentKey(selection: ChannelPrdSelection): string {
  return JSON.stringify(selection);
}
function url(selection: ChannelPrdSelection, resource: string) {
  const params = new URLSearchParams({ thread_id: selection.threadId, revision: String(selection.revision), digest: selection.digest });
  return `/api/projects/${encodeURIComponent(selection.projectId || "default")}/channels/${encodeURIComponent(selection.channelId)}/${resource}?${params}`;
}
export function getChannelPrdDocument(selection: ChannelPrdSelection, signal?: AbortSignal) {
  return cachedGetJson<ChannelPrdDocument>(url(selection, "prd-preview"), { bypassCache: true, signal });
}
export function getChannelDelivery(selection: ChannelPrdSelection, signal?: AbortSignal) {
  return cachedGetJson<ChannelDeliveryEvidence>(url(selection, "delivery"), { bypassCache: true, signal });
}
export function getChannelPrdEvaluation(selection: ChannelPrdSelection, signal?: AbortSignal) {
  return cachedGetJson<ChannelPrdEvaluationView>(url(selection, "prd-evaluation"), { bypassCache: true, signal });
}
