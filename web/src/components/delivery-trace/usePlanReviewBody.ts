import { useEffect, useState } from "react";
import { getPlanPreview } from "../../api/client";
import type { PlanPreview } from "../../api/types";

/** Full rendering is an explicit reading action, independent of the approval envelope. */
export function usePlanReviewBody(envelope: PlanPreview | null, projectId: string | undefined, requested: boolean) {
  const key = envelope ? JSON.stringify([projectId, envelope.plan_id, envelope.requested_event_id,
    envelope.resolved_event_id, envelope.status, envelope.artifacts]) : "";
  const [result, setResult] = useState<{ key: string; body?: PlanPreview; error?: string }>({ key: "" });
  const loaded = result.key === key ? result : null;
  useEffect(() => {
    if (!envelope || envelope.content_state !== "deferred" || !requested || loaded?.body) return;
    const controller = new AbortController();
    setResult({ key });
    void getPlanPreview(envelope.plan_id, projectId, false, controller.signal).then(body => {
      if (controller.signal.aborted) return;
      if (body.requested_event_id !== envelope.requested_event_id || body.status !== envelope.status
          || body.resolved_event_id !== envelope.resolved_event_id) {
        setResult({ key, error: "计划审批版本已改变，请关闭后重新打开预览。" });
        return;
      }
      setResult({ key, body });
    }).catch(error => {
      if (!controller.signal.aborted) setResult({ key, error: error instanceof Error ? error.message : String(error) });
    });
    return () => controller.abort();
    // A result does not start a new fetch; only the selected identity/view does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, requested]);
  return { preview: loaded?.body ? { ...envelope!, ...loaded.body, content_state: undefined } : envelope,
    loading: Boolean(envelope?.content_state === "deferred" && requested && !loaded?.body && !loaded?.error),
    error: loaded?.error || "" };
}
