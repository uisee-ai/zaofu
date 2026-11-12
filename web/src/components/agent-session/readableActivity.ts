import type { AgentSessionPart, AgentSessionRun, AgentSessionStatus } from "./types";
import { isToolPart } from "./toolGrouping.js";

type Row = Record<string, unknown>;
function record(value: unknown): Row { return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {}; }
function text(value: unknown): string { return typeof value === "string" ? value : ""; }

export interface ActivityStep {
  id: string; title: string; state: string; index: number; total: number; completed: number;
  retry: boolean; restored: boolean; reason: string;
}
export interface ReadableTool { id: string; title: string; detail: string; state: string; part: AgentSessionPart }
export interface ReadableActivity {
  title: string; working: boolean; steps: ActivityStep[]; notes: string[]; tools: ReadableTool[];
  completed?: number; total?: number; failed: boolean;
}

const PLACEHOLDERS = /^(?:running|started|working|thinking|queued|submitted|completed|done|codex-headless|claude-headless|已开始|正在执行|处理中)[.!…\s]*$/i;
export function readableProgress(content: string): string {
  const clean = content.split("<!-- zf:channel-contract -->", 1)[0].trim();
  if (clean.length < 4 || PLACEHOLDERS.test(clean) || /^[{\[]/.test(clean)) return "";
  if (/(?:evt|reply|synth|run)-[a-f\d]{8,}/i.test(clean)) return "";
  return clean;
}

function toolTitle(part: AgentSessionPart, raw: Row): string {
  const name = `${part.toolName || ""} ${part.title} ${text(raw.tool)} ${text(record(raw.params).name)}`.toLowerCase();
  if (/search|检索|搜索|web_fetch|browse/.test(name)) return "检索资料";
  if (/read|读取|file_read|查看文件/.test(name)) return "读取文件";
  if (/test|测试|verify|check/.test(name)) return "执行验证";
  if (/edit|write|patch|file_change|修改|写入/.test(name)) return "更新文件";
  if (/command|exec|shell|bash|terminal/.test(name)) return "执行检查";
  return "处理工具活动";
}

function stateTitle(status: AgentSessionStatus): string {
  if (status === "failed") return "本次执行未完成";
  if (status === "cancelled") return "本次执行已停止";
  if (status === "waiting_input") return "等待你的回答";
  if (status === "queued") return "等待开始处理";
  if (status === "completed") return "本次处理已完成";
  return "正在处理你的消息";
}

/** Display only supplied public progress and verified phase receipts. */
export function readableActivity(run: AgentSessionRun): ReadableActivity {
  const phases = new Map<string, ActivityStep>();
  const notes = new Map<string, string>();
  const tools = new Map<string, ReadableTool>();
  for (const part of run.parts) {
    const refs = record(part.refs), raw = record(refs.raw), params = record(raw.params);
    const activity = record(raw.activity);
    if (activity.kind === "synthesis" && text(activity.id) && text(activity.title)) {
      phases.set(text(activity.id), { id: text(activity.id), title: text(activity.title), state: text(activity.state),
        index: Number(activity.index || 0), total: Number(activity.total || 0), completed: Number(activity.completed || 0),
        retry: activity.retry === true, restored: activity.restored === true, reason: text(activity.reason) });
      continue;
    }
    const publicNote = record(raw.public_commentary);
    if (publicNote.id) {
      notes.set(text(publicNote.id), part.content || part.summary || "");
    } else if (part.kind === "status" && text(raw.method).endsWith("item/agentMessage/delta") && params.itemId) {
      const id = text(params.itemId);
      notes.set(id, (notes.get(id) || "") + (part.content || ""));
    } else if (part.kind === "status") {
      const note = readableProgress(part.content || part.summary || "");
      if (note) notes.set(part.id, note);
    }
    if (!isToolPart(part)) continue;
    const native = record(params.item);
    const id = part.toolCallId || text(params.itemId) || text(native.id) || part.id;
    const previous = tools.get(id);
    const input = record(refs.input);
    const path = text(input.file_path || input.path || input.filename);
    const filename = path.split(/[\\/]/).at(-1) || "";
    const detail = filename && !/[a-f\d]{16,}/i.test(filename) ? filename : previous?.detail || "";
    const title = toolTitle(part, raw);
    const completionConfirmed = refs.completion_confirmed === true || raw.method === "item/completed"
      || part.sourceEvent?.type === "agent.session.tool.completed" || ["tool_result", "test_result"].includes(part.kind);
    const state = part.state === "completed" && !completionConfirmed ? "unknown" : part.state;
    tools.set(id, { id, title: title === "处理工具活动" && previous ? previous.title : title,
      detail, state, part });
  }
  const steps = [...phases.values()].sort((a, b) => a.index - b.index);
  const publicNotes = [...new Set([...notes.values()].map(readableProgress).filter(Boolean))];
  const working = run.status === "streaming" || run.status === "submitted";
  if (!working) for (const step of steps) {
    if (step.state === "started") step.state = run.status === "cancelled" ? "cancelled" : run.status === "failed" ? "failed" : "unconfirmed";
  }
  const current = [...steps].reverse().find(step => step.state === "started");
  const last = steps.at(-1);
  const failed = run.status === "failed" || last?.state === "failed";
  const total = steps.length ? Math.max(...steps.map(step => step.total)) : undefined;
  const completed = steps.length ? Math.max(...steps.map(step => step.completed)) : undefined;
  const fullTitle = working
    ? current?.title || (steps.length && completed === total ? "已保存各部分，正在提交完整性校验" : publicNotes.at(-1) || stateTitle(run.status))
    : stateTitle(run.status);
  const title = fullTitle.length > 120 ? fullTitle.slice(0, 80) + "…" : fullTitle;
  return { title, working, steps, notes: publicNotes, tools: [...tools.values()], completed, total, failed };
}
