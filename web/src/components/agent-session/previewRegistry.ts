export type PreviewProfile = "markdown" | "code" | "diff" | "log" | "json" | "yaml" | "trace" | "artifact" | "report" | "text";

export interface PreviewItem {
  kind: string;
  id: string;
  name: string;
  meta: string;
  profile: PreviewProfile;
  ref: Record<string, unknown>;
}

export function previewItemsFromRefs(refs?: Record<string, unknown>): PreviewItem[] {
  if (!refs) return [];
  const items: PreviewItem[] = [];
  for (const { kind, item } of refGroups(refs)) {
    const name = refString(item, "name") || refString(item, "filename") || refString(item, "path");
    const id = (
      refString(item, "attachment_id")
      || refString(item, "artifact_id")
      || refString(item, "event_id")
      || refString(item, "id")
      || name
    );
    const mime = refString(item, "mime") || refString(item, "type") || refString(item, "content_type") || refString(item, "kind");
    const profile = previewProfileForRef({ ...item, kind: mime || kind, path: name || refString(item, "path") });
    items.push({
      kind,
      id,
      name: name || id || kind,
      meta: [profile, mime, refSizeLabel(Number(item.size ?? item.bytes ?? 0))].filter(Boolean).join(" / "),
      profile,
      ref: item,
    });
  }
  for (const key of ["task_id", "trace_id", "event_id", "source_event_id", "request_event_id", "channel_last_event_id", "artifact_id", "path", "file", "report_id", "snapshot_ref", "provider_session_id"]) {
    const value = refs[key];
    if (typeof value !== "string" || !value.trim()) continue;
    const profile = previewProfileForRef({ [key]: value, path: value, kind: key });
    items.push({
      kind: key,
      id: value,
      name: value,
      meta: profile,
      profile,
      ref: { [key]: value },
    });
  }
  return dedupePreviewItems(items);
}

export function previewProfileForRef(ref: Record<string, unknown>): PreviewProfile {
  const path = refString(ref, "path") || refString(ref, "file") || refString(ref, "name") || refString(ref, "filename");
  const kind = `${refString(ref, "kind")} ${refString(ref, "mime")} ${refString(ref, "type")} ${path}`.toLowerCase();
  if (kind.includes("diff") || kind.endsWith(".patch") || kind.endsWith(".diff")) return "diff";
  if (kind.includes("markdown") || kind.endsWith(".md")) return "markdown";
  if (kind.includes("json") || kind.endsWith(".json")) return "json";
  if (kind.includes("yaml") || kind.includes("yml") || kind.endsWith(".yaml") || kind.endsWith(".yml")) return "yaml";
  if (kind.includes("log") || kind.endsWith(".log") || kind.includes("stdout") || kind.includes("stderr")) return "log";
  if (kind.includes("trace") || refString(ref, "trace_id")) return "trace";
  if (kind.includes("report") || refString(ref, "report_id")) return "report";
  if (/\.(py|ts|tsx|js|jsx|rs|go|java|rb|php|css|html|sh|toml|ini|cfg)$/i.test(path)) return "code";
  if (refString(ref, "artifact_id")) return "artifact";
  return "text";
}

export function actionImpactRows(action: string, payload: Record<string, unknown>): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [];
  const push = (label: string, value: unknown) => {
    const text = value === null || value === undefined || value === "" ? "" : String(value);
    if (text) rows.push({ label, value: text });
  };
  push("action", action);
  if (action === "create-task") {
    push("title", payload.title);
    push("assignee", payload.assigned_to || payload.assignee_id);
    push("verification", recordValue(payload.contract)?.verification);
  } else if (action === "update-task") {
    push("task", payload.task_id);
    for (const key of ["status", "assigned_to", "blocked_reason", "priority"]) push(key, payload[key]);
  } else if (action === "workflow-invoke") {
    push("workflow", payload.workflow_id || payload.pattern_id || payload.stage_id);
    push("task", payload.task_id);
    push("channel", payload.channel_id);
  } else if (action === "apply-patch-proposal") {
    push("patch", payload.patch_ref || payload.artifact_id || payload.path);
    push("mode", "gated preview only");
  } else {
    for (const key of Object.keys(payload).slice(0, 6)) push(key, payload[key]);
  }
  return rows;
}

function refGroups(refs: Record<string, unknown>): Array<{ kind: string; item: Record<string, unknown> }> {
  const groups: Array<{ kind: string; item: Record<string, unknown> }> = [];
  for (const [key, kind] of [
    ["attachments", "attachment"],
    ["artifacts", "artifact"],
    ["artifact_refs", "artifact"],
    ["preview_refs", "preview"],
    ["report_refs", "report"],
    ["event_refs", "event"],
    ["workflow_refs", "workflow"],
    ["replan_refs", "replan"],
    ["task_refs", "task"],
    ["trace_refs", "trace"],
    ["diff_refs", "diff"],
    ["test_refs", "test"],
    ["log_refs", "log"],
    ["evidence_refs", "evidence"],
    ["source_refs", "source"],
  ] as const) {
    for (const item of asPreviewRefArray(refs[key], kind)) groups.push({ kind, item });
  }
  return groups;
}

function dedupePreviewItems(items: PreviewItem[]): PreviewItem[] {
  const seen = new Set<string>();
  const out: PreviewItem[] = [];
  for (const item of items) {
    const key = `${item.kind}:${item.id}:${item.name}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)));
}

function asPreviewRefArray(value: unknown, kind: string): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  const refs: Record<string, unknown>[] = [];
  for (const item of value) {
    if (item && typeof item === "object" && !Array.isArray(item)) {
      refs.push(item as Record<string, unknown>);
      continue;
    }
    if (typeof item !== "string" || !item.trim()) continue;
    refs.push(stringRef(kind, item));
  }
  return refs;
}

function stringRef(kind: string, value: string): Record<string, unknown> {
  if (kind === "task") return { task_id: value, name: value, kind };
  if (kind === "trace") return { trace_id: value, name: value, kind };
  if (kind === "event" || kind === "workflow" || kind === "replan") return { event_id: value, name: value, kind };
  if (kind === "report") return { report_id: value, name: value, kind };
  return { path: value, name: value, kind };
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function refString(item: Record<string, unknown>, key: string): string {
  const value = item[key];
  if (value === null || value === undefined) return "";
  return String(value);
}

function refSizeLabel(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
