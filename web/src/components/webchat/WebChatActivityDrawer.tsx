import { Activity, AlertCircle, Bot, ExternalLink, Radio, Send, Square, X, Zap } from "lucide-react";
import { useState } from "react";
import { controlWebChatTurn, enqueueWebChatMessage, postAction } from "../../api/client";
import type { WebChatChildActivity } from "../../api/types";
import { useWebChatDrawer } from "./useWebChatDrawer";

function kindLabel(value: string): string {
  if (value === "delivery_worker") return "Delivery worker";
  if (value === "channel_member") return "Channel member";
  return "Analysis child";
}

export function WebChatActivityDrawer({
  activities,
  onClose,
  onOpenThread,
  onUpdated,
  projectId,
}: {
  activities: WebChatChildActivity[];
  onClose: () => void;
  onOpenThread: (activity: WebChatChildActivity) => void;
  onUpdated: () => Promise<void> | void;
  projectId: string;
}) {
  const drawerRef = useWebChatDrawer(onClose);
  const [selectedId, setSelectedId] = useState(activities[0]?.activity_id ?? "");
  const [controlMessage, setControlMessage] = useState("");
  const [busyControl, setBusyControl] = useState("");
  const [notice, setNotice] = useState("");
  const selected = activities.find((item) => item.activity_id === selectedId) ?? activities[0];

  async function runControl(control: NonNullable<WebChatChildActivity["controls"]>[number]) {
    const operation = control.operation;
    if (control.requires_message && !controlMessage.trim()) {
      setNotice("Add a message before sending this control.");
      return;
    }
    const idempotencyKey = `child-${selected?.activity_id || "activity"}-${operation}-${Date.now()}`;
    setBusyControl(operation);
    setNotice("");
    try {
      if (control.route === "controlled_action" && control.action) {
        const result = await postAction(control.action, {
          ...(control.payload || {}),
          ...(control.requires_message ? { message: controlMessage.trim() } : {}),
          idempotency_key: idempotencyKey,
        }, projectId);
        if (!result.ok) throw new Error(result.reason || `${control.action} was rejected`);
      } else if (control.route === "webchat_turn" && control.target_id) {
        await controlWebChatTurn(projectId, control.target_id, {
          operation,
          message: controlMessage.trim(),
          idempotency_key: idempotencyKey,
        });
      } else if (control.route === "webchat_queue") {
        await enqueueWebChatMessage(projectId, {
          ...(control.payload || {}),
          message: controlMessage.trim(),
          idempotency_key: idempotencyKey,
        });
      } else {
        throw new Error("This activity does not expose a sanctioned control route.");
      }
      setControlMessage("");
      setNotice(`${operation} request accepted.`);
      await onUpdated();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyControl("");
    }
  }
  return (
    <aside aria-label="Child activity" aria-modal="true" className="webchat-drawer" ref={drawerRef} role="dialog" tabIndex={-1}>
      <header className="webchat-drawer-header">
        <span><Activity size={16} /> Activity</span>
        <button aria-label="Close activity" className="agent-window-button ghost" type="button" onClick={onClose}>
          <X size={16} />
        </button>
      </header>
      <div className="webchat-drawer-body">
        <div className="webchat-resource-list">
          {activities.length ? activities.map((item) => (
            <button
              className={`webchat-activity-row ${item.activity_id === selected?.activity_id ? "active" : ""}`}
              key={item.activity_id}
              type="button"
              onClick={() => setSelectedId(item.activity_id)}
            >
              <span className={`webchat-activity-dot ${item.status}`} aria-hidden="true" />
              <strong>{item.role || item.activity_id}</strong>
              <small>{kindLabel(item.kind)} · {item.status}</small>
            </button>
          )) : <p className="muted">No child activity for this conversation.</p>}
        </div>
        {selected ? (
          <section className="webchat-resource-detail">
            <div className="webchat-resource-title">
              <strong>{selected.role || selected.activity_id}</strong>
              <span className="webchat-activity-kind">{kindLabel(selected.kind)}</span>
            </div>
            <dl className="webchat-resource-meta">
              <div><dt>Provider</dt><dd>{selected.provider || "-"}</dd></div>
              <div><dt>Permission</dt><dd>{selected.permission_profile || "-"}</dd></div>
              <div><dt>Stage</dt><dd>{selected.current_stage || "-"}</dd></div>
              <div><dt>Heartbeat</dt><dd>{selected.heartbeat_at || selected.updated_at || "-"}</dd></div>
            </dl>
            <div className="webchat-capability-list">
              {(selected.capabilities || []).map((item) => <span key={item}>{item}</span>)}
            </div>
            {selected.thread_id ? (
              <button className="webchat-child-open" type="button" onClick={() => onOpenThread(selected)}>
                <ExternalLink size={14} /> Open child thread
              </button>
            ) : null}
            {(selected.controls || []).length ? (
              <div className="webchat-child-controls">
                {(selected.controls || []).some((control) => control.requires_message) ? (
                  <label>
                    Control message
                    <textarea
                      placeholder="Tell this child what should change"
                      rows={3}
                      value={controlMessage}
                      onChange={(event) => setControlMessage(event.target.value)}
                    />
                  </label>
                ) : null}
                <div>
                  {(selected.controls || []).map((control) => (
                    <button
                      disabled={Boolean(busyControl)}
                      key={`${control.route}:${control.operation}`}
                      type="button"
                      onClick={() => void runControl(control)}
                    >
                      {control.operation === "cancel" ? <Square size={14} /> : control.operation === "interrupt" ? <Zap size={14} /> : <Send size={14} />}
                      {control.operation}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            {notice ? <p className="webchat-child-control-notice" role="status">{notice}</p> : null}
            {selected.why_not_done ? (
              <div className="webchat-why-not"><AlertCircle size={15} /><span>{selected.why_not_done}</span></div>
            ) : (
              <div className="webchat-observed-terminal"><Radio size={15} /><span>Host-observed status: {selected.status}</span></div>
            )}
            {(selected.artifact_refs || []).length ? (
              <div className="webchat-child-artifacts">
                <span><Bot size={14} /> Output</span>
                {(selected.artifact_refs || []).map((ref) => <code key={ref}>{ref}</code>)}
              </div>
            ) : null}
          </section>
        ) : null}
      </div>
    </aside>
  );
}
