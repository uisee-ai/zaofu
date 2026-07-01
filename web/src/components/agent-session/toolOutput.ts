// Pure helpers for rendering tool/command output in the shared
// AgentSessionTimeline. No React, no DOM — kept separate so it is unit
// testable (tests/agentToolOutput.test.ts) and reusable across the
// channel + orchestrator surfaces that mount the timeline.
//
// Tool output preview helpers with line/char caps, hidden-count stats,
// and duration formatting for ZaoFu's AgentSessionPart model.

export const OUTPUT_PREVIEW_LINE_LIMIT = 80;
export const OUTPUT_PREVIEW_CHAR_LIMIT = 12_000;

export interface OutputPreview {
  text: string;
  isTruncated: boolean;
  lineCount: number;
  charCount: number;
  shownLineCount: number;
  shownCharCount: number;
  hiddenLineCount: number;
  hiddenCharCount: number;
}

export interface RawOutputRef {
  raw_ref: string;
  meta_ref?: string;
  sha256?: string;
  byte_count?: number;
  line_count?: number;
  mime?: string;
  encoding?: string;
  preview?: string;
  head?: string;
  tail?: string;
  truncated?: boolean;
}

/**
 * If the string is valid JSON, return its 2-space-indented form;
 * otherwise return it verbatim. A compact one-line JSON payload renders
 * inside a <pre> as one horizontal-scrolling line — pretty-printing keeps
 * it readable.
 */
export function prettyPrintIfJson(s: string): string {
  const trimmed = s.trim();
  if (!trimmed || (trimmed[0] !== "{" && trimmed[0] !== "[")) return s;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return s;
  }
}

/**
 * Clamp output to a line + char budget. `expanded` returns the full text.
 * Reports how much is shown vs hidden so the UI can label the preview.
 */
export function getOutputPreview(output: string, expanded = false): OutputPreview {
  const lines = output.length === 0 ? [] : output.split("\n");
  const lineCount = lines.length;
  const charCount = output.length;

  if (expanded || (lineCount <= OUTPUT_PREVIEW_LINE_LIMIT && charCount <= OUTPUT_PREVIEW_CHAR_LIMIT)) {
    return {
      text: output,
      isTruncated: false,
      lineCount,
      charCount,
      shownLineCount: lineCount,
      shownCharCount: charCount,
      hiddenLineCount: 0,
      hiddenCharCount: 0,
    };
  }

  let text = lineCount > OUTPUT_PREVIEW_LINE_LIMIT
    ? lines.slice(0, OUTPUT_PREVIEW_LINE_LIMIT).join("\n")
    : output;
  if (text.length > OUTPUT_PREVIEW_CHAR_LIMIT) {
    text = text.slice(0, OUTPUT_PREVIEW_CHAR_LIMIT).trimEnd();
  }

  const shownLineCount = text.length === 0 ? 0 : text.split("\n").length;
  const shownCharCount = text.length;
  return {
    text,
    isTruncated: shownCharCount < charCount,
    lineCount,
    charCount,
    shownLineCount,
    shownCharCount,
    hiddenLineCount: Math.max(0, lineCount - shownLineCount),
    hiddenCharCount: Math.max(0, charCount - shownCharCount),
  };
}

function formatCount(count: number, unit: string): string {
  return `${count.toLocaleString()} ${unit}${count === 1 ? "" : "s"}`;
}

export function formatOutputStats(preview: OutputPreview): string {
  if (!preview.isTruncated) {
    return `${formatCount(preview.lineCount, "line")} / ${formatCount(preview.charCount, "char")}`;
  }
  const hidden: string[] = [];
  if (preview.hiddenLineCount > 0) hidden.push(`${formatCount(preview.hiddenLineCount, "line")} hidden`);
  if (preview.hiddenCharCount > 0) hidden.push(`${formatCount(preview.hiddenCharCount, "char")} hidden`);
  return `${formatCount(preview.shownLineCount, "line")} / ${formatCount(preview.shownCharCount, "char")} shown; ${hidden.join(", ")}`;
}

export function rawOutputRefFromRefs(refs?: Record<string, unknown>): RawOutputRef | null {
  const raw = refs?.raw_output;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const row = raw as Record<string, unknown>;
  const rawRef = typeof row.raw_ref === "string" ? row.raw_ref.trim() : "";
  if (!rawRef) return null;
  return {
    raw_ref: rawRef,
    meta_ref: typeof row.meta_ref === "string" ? row.meta_ref : undefined,
    sha256: typeof row.sha256 === "string" ? row.sha256 : undefined,
    byte_count: numberValue(row.byte_count),
    line_count: numberValue(row.line_count),
    mime: typeof row.mime === "string" ? row.mime : undefined,
    encoding: typeof row.encoding === "string" ? row.encoding : undefined,
    preview: typeof row.preview === "string" ? row.preview : undefined,
    head: typeof row.head === "string" ? row.head : undefined,
    tail: typeof row.tail === "string" ? row.tail : undefined,
    truncated: row.truncated === true,
  };
}

export function rawOutputLabel(raw: RawOutputRef): string {
  const parts: string[] = [];
  if (raw.line_count !== undefined) parts.push(`${raw.line_count.toLocaleString()} lines`);
  if (raw.byte_count !== undefined) parts.push(`${raw.byte_count.toLocaleString()} bytes`);
  if (raw.sha256) parts.push(`sha256 ${raw.sha256.slice(0, 12)}`);
  return parts.join(" / ") || "raw output";
}

/**
 * Human-friendly elapsed/duration: ms under 1s, one decimal under 10s,
 * whole seconds under a minute, then m s / h m.
 */
export function formatToolDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0ms";
  if (seconds < 1) return `${Math.max(1, Math.round(seconds * 1000))}ms`;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const totalSeconds = Math.round(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  if (totalSeconds < 3600) return `${minutes}m ${totalSeconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function numberValue(value: unknown): number | undefined {
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}
