import { useEffect, useRef, useState, type ReactNode } from "react";
import { Search, Bot } from "lucide-react";
import type { AgentSummary } from "../../api/types";
import { needsAttention, contextPercent } from "../../app/cockpitModel";
import { formatTokens } from "../../lib/format";
import { useUiPreferences } from "../../app/UiPreferences";

export function AgentFleetTable({ workers, onSelect, onOpenTask }: { workers: AgentSummary[]; onSelect: (id: string) => void; onOpenTask: (id: string) => void }) {
  const { t } = useUiPreferences();
  const [search, setSearch] = useState("");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const visible = workers.filter((worker) => (!attentionOnly || needsAttention(worker.attention_state)) &&
    [worker.instance_id, worker.parent_role, worker.backend, worker.task_id, worker.active_task].join(" ").toLowerCase().includes(search.toLowerCase()));
  return <section className="agent-fleet-table" aria-label={t("Agent fleet")}>
    <div className="workspace-list-toolbar">
      <label className="workspace-search"><Search size={14} aria-hidden="true" /><input aria-label={t("Search agents")} placeholder={t("Search agents…")} value={search} onChange={(event) => setSearch(event.target.value)} /></label>
      <div className="workspace-segments" aria-label={t("Agent filter")} role="group">
        <button type="button" aria-pressed={!attentionOnly} onClick={() => setAttentionOnly(false)}>{t("All")} <span>{workers.length}</span></button>
        <button type="button" aria-pressed={attentionOnly} onClick={() => setAttentionOnly(true)}>{t("Needs attention")}</button>
      </div>
    </div>
    <div className="workspace-table-wrap"><table className="workspace-data-table"><thead><tr>{["Agent", "Provider", "Status", "Task", "Context", "Tokens"].map((label) => <th key={label}>{t(label)}</th>)}</tr></thead>
      <tbody>{visible.map((worker) => {
        const task = worker.task_id || worker.active_task;
        return <tr key={worker.instance_id}>
          <td><button className="fleet-agent-open" type="button" onClick={() => onSelect(worker.instance_id)}><span className="fleet-agent-icon"><Bot size={17} aria-hidden="true" /></span><span><strong>{worker.instance_id}</strong><small>{worker.parent_role || worker.role_type || "—"}</small></span></button></td>
          <td>{worker.backend || "—"}</td>
          <td><span className={`overview-state ${needsAttention(worker.attention_state) ? "state-blocked" : ""}`}>{worker.stale ? `${t("Last known")}: ` : ""}{worker.lifecycle_state || worker.state || "—"}</span></td>
          <td>{task ? <button className="workspace-text-link" type="button" onClick={() => onOpenTask(task)}>{task}</button> : "—"}</td>
          <td>{worker.context_usage_ratio == null ? "—" : contextPercent(worker.context_usage_ratio)}</td>
          <td>{worker.cost ? formatTokens((worker.cost.input_tokens ?? 0) + (worker.cost.output_tokens ?? 0)) : "—"}</td>
        </tr>;
      })}</tbody></table>
      {!visible.length && <p className="workspace-empty-inline">{t("No matching agents")}</p>}
    </div>
  </section>;
}

export function WorkerDetailsDialog({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const { t } = useUiPreferences();
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const el = ref.current;
    el?.showModal();
    return () => {
      el?.close();
      // React may detach the dialog before the browser restores native focus.
      window.requestAnimationFrame(() => {
        if (opener?.isConnected && !el?.open) opener.focus({ preventScroll: true });
      });
    };
  }, []);
  return <dialog ref={ref} className="worker-detail-drawer" aria-label={t("Worker details")} onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget && event.clientX < event.currentTarget.getBoundingClientRect().left) onClose(); }}>{children}</dialog>;
}
