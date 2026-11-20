type Row = Record<string, unknown>;
const text = (value: unknown) => typeof value === "string" ? value : "";
const activeStates = new Set(["pending", "queued", "running", "started", "retrying"]);
const failedStates = new Set(["failed", "rejected", "escalated", "timeout", "timed_out"]);
export const isReplyActive = (row: Row) => activeStates.has(text(row.status));
export const isReplyFailure = (row: Row) => failedStates.has(text(row.status))
  && !["cancelled", "superseded"].includes(text(row.terminal_disposition))
  && !["cancelled", "superseded"].includes(text(row.failure_category))
  && text(row.failure_status) !== "cancelled";

/** A new message to the same role is not recovery of an earlier request. */
export function channelReplyHealth(rows: Row[], threadId: string) {
  const requests = new Map<string, Row>();
  rows.forEach((row, index) => {
    if ((text(row.thread_id) || "main") !== threadId) return;
    const key = text(row.request_id) || text(row.event_id) || `unidentified-${index}`;
    const prior = requests.get(key);
    const generation = Number(row.run_generation || 1);
    const priorGeneration = Number(prior?.run_generation || 1);
    if (!prior || generation > priorGeneration || (generation === priorGeneration
      && text(row.updated_at || row.ts) >= text(prior.updated_at || prior.ts))) requests.set(key, row);
  });
  const all = [...requests.values()];
  const active = all.filter(isReplyActive);
  const failed = all.filter(isReplyFailure);
  const settled = all.filter((row) => !isReplyActive(row) && !isReplyFailure(row));
  return { all, active, failed, settled, running: active.filter((row) => ["running", "started"].includes(text(row.status))),
    waiting: active.filter((row) => !["running", "started"].includes(text(row.status))) };
}

export function replyFailurePresentation(row: Row) {
  const category = text(row.failure_category);
  const status = text(row.failure_status);
  const label = category === "timeout" || /timeout|timed_out/.test(status) ? "回复超时"
    : category === "rate_limit" || status === "rate_limited" ? "Provider 限流"
    : category === "dispatch" ? "回复启动失败"
    : category === "contract" || status === "contract_invalid" ? "回复格式不完整"
    : category === "provider" ? "Provider 错误" : "回复失败";
  const advice = status === "delivery_unknown" ? "原回复投递结果未知。可核验原生中止回执；仅确认原 turn 已中止后才会重试。"
    : row.retryable === false ? "需要先处理配置、权限或回复内容问题；不会自动重试。"
    : "是否继续重试由运行时决定；此处不会自动发起新调用。";
  return { label, advice };
}
