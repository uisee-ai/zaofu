// Control-loop churn events carry no operator-visible state. Keep this one
// denylist shared by global slice invalidation and scoped Delivery refresh.
const REFRESH_NOISE_EVENT_TYPES = new Set([
  "task.requeue.skipped",
  "worker.heartbeat",
  "run.manager.tick.completed",
]);

export function isRefreshNoiseEventType(eventType: string): boolean {
  return REFRESH_NOISE_EVENT_TYPES.has(eventType);
}

export function isTaskBusinessEvent(type: string): boolean {
  return /^(task|feature|dev|test|verify|review|judge|gate|candidate|fanout|workdir|run|ship|workflow|handoff|assignment)\./.test(type)
    && !isRefreshNoiseEventType(type);
}
