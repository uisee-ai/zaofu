import { useEffect, useState, type ReactNode } from "react";
import { Check, ChevronRight, Circle, CircleSlash, Clock3, Loader2, XCircle } from "lucide-react";
import type { AgentSessionPart, AgentSessionRun } from "./types";
import { readableActivity } from "./readableActivity";
import { runStartTimestamp } from "./liveRunIndicator";

export function ReadableAgentActivity({ run, onStop, renderTechnicalPart }: {
  run: AgentSessionRun; onStop?: () => void; renderTechnicalPart: (part: AgentSessionPart) => ReactNode;
}) {
  const activity = readableActivity(run);
  const [open, setOpen] = useState(false);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!activity.working) return;
    const timer = window.setInterval(() => tick(n => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [activity.working]);
  const start = Date.parse(runStartTimestamp(run) || "");
  const end = activity.working ? Date.now() : Date.parse(run.updatedAt || run.startedAt || "");
  const seconds = Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, Math.floor((end - start) / 1000)) : undefined;
  const elapsed = seconds === undefined ? "" : seconds < 60 ? `${seconds}秒` : `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
  if (!activity.working && !activity.failed && run.status !== "cancelled"
    && !activity.steps.length && !activity.tools.length && !activity.notes.length) return null;
  const Icon = activity.working ? Loader2 : activity.failed ? XCircle : run.status === "completed" ? Check : run.status === "cancelled" ? CircleSlash : Clock3;
  return <section className={`agent-readable-activity ${activity.failed ? "has-error" : ""}`} data-testid="agent-readable-activity">
    <div className="agent-readable-activity-heading">
      <button type="button" className="agent-readable-activity-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={14} className={open ? "is-open" : ""} />
        <Icon size={15} className={activity.working ? "agent-activity-spinner" : ""} />
        <span className="agent-readable-activity-title">{activity.title}</span>
        {elapsed ? <small>{elapsed}</small> : null}
      </button>
      {onStop && activity.working ? <button type="button" className="agent-inline-button" aria-label="Cancel" onClick={onStop}>停止</button> : null}
    </div>
    <div className="agent-readable-activity-caption">
      {activity.total ? <span>已保存 {activity.completed} / {activity.total} 部分</span>
        : activity.tools.length ? <span>{activity.tools.length} 项活动</span> : null}
      <button type="button" className="agent-inline-button" onClick={() => setOpen(!open)}>{open ? "收起过程" : "展开过程"}</button>
    </div>
    {open ? <div className="agent-readable-activity-body" data-testid="agent-readable-activity-body">
      {activity.steps.length ? <ol className="agent-readable-steps">
        {activity.steps.map(step => <li key={step.id} data-state={step.state}>
          {step.state === "completed" ? <Check size={15} /> : step.state === "started" ? <Loader2 size={15} className="agent-activity-spinner" /> : step.state === "failed" ? <XCircle size={15} /> : step.state === "cancelled" ? <CircleSlash size={15} /> : <Circle size={15} />}
          <div><strong>{step.title}</strong>
            {step.state === "completed" ? <small>{step.restored ? "已恢复先前保存的部分" : activity.working ? "该部分已保存，待最终完整性校验" : "该部分已保存"}</small> : null}
            {step.retry ? <small>正在重试当前部分，已保存内容保留</small> : null}
            {step.reason ? <details><summary>查看原因</summary><p>{step.reason}</p></details> : null}
          </div>
        </li>)}
      </ol> : null}
      {activity.notes.length ? <div className="agent-public-progress"><strong>进展说明</strong>{activity.notes.map((note, i) => <p key={i}>{note}</p>)}</div> : null}
      {activity.tools.length ? <details className="agent-readable-tools"><summary>查看 {activity.tools.length} 项活动</summary>
        {activity.tools.map(tool => <div key={tool.id} className="agent-readable-tool">
          {tool.state === "failed" ? <XCircle size={14} /> : ["streaming", "submitted"].includes(tool.state) && activity.working ? <Loader2 size={14} className="agent-activity-spinner" /> : tool.state === "completed" ? <Check size={14} /> : tool.state === "cancelled" ? <CircleSlash size={14} /> : <Clock3 size={14} />}
          <div><span>{tool.title}{tool.detail ? ` · ${tool.detail}` : ""}</span>
            {!["completed", "failed", "cancelled"].includes(tool.state) && !activity.working ? <small>结果未确认</small> : null}
            <details><summary>技术详情</summary>{renderTechnicalPart(tool.part)}</details>
          </div>
        </div>)}
      </details> : null}
    </div> : null}
  </section>;
}
