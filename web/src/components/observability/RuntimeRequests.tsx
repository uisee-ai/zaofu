import { useState } from "react";
import { postAction } from "../../api/client";
import type { OperationsObservability } from "../../api/types";

export function RuntimeRequests({ projectId, requests, onChanged }: {
  projectId?: string;
  requests: OperationsObservability["source_requests"];
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [history, setHistory] = useState(false);
  const items = (requests?.items || []).filter(item => history
    || ["pending", "processing", "failed"].includes(item.status));
  return <section className="subsection" data-testid="runtime-source-requests">
    <div className="inline-heading"><h3>运行处置与待核实记录</h3></div>
    <p className="muted">查看运行请求及处理结果。未提供处置操作的旧记录，需要结合原始上下文核实。</p>
    <label><input type="checkbox" checked={history} onChange={event => setHistory(event.target.checked)} /> 包含历史</label>
    {error && <p className="error">{error}</p>}
    {!requests?.available ? <p className="muted">运行处置状态不可用。</p>
      : !items.length ? <p className="muted">暂无运行处置请求。</p>
        : items.map(item => <article key={item.id} data-runtime-request-id={item.id}>
          <h4>{item.title}</h4>
          <p>{item.summary}</p>
          <p>状态：{item.status === "processing" ? "决定已提交，等待运行系统处理" : item.status === "failed" ? "处置失败" : item.status}</p>
          {item.resolution && <p>{item.resolution}</p>}
          {item.verification_required && <p>待核实：当前记录未提供可执行的处置合同。</p>}
          <details><summary>来源与记录</summary><p>{item.created_event_id}</p><p>{item.run_id || item.workflow_run_id}</p><p>{item.approval_ref}</p></details>
          {item.status === "pending" && (item.actions || []).map(action => {
            const name = String(action.action || "");
            if (!name) return null;
            return <button key={name} type="button" disabled={Boolean(busy)} onClick={async () => {
              setBusy(item.id); setError("");
              try {
                const result = await postAction(name, {
                  source: "runtime-operations", decision_token: item.decision_token,
                  checkpoint_id: item.checkpoint_id, created_event_id: item.created_event_id,
                }, projectId);
                if (result.ok === false) setError(result.reason || "处置未成功，请检查当前请求状态。");
                await onChanged();
              } catch (reason) { setError(String(reason)); }
              finally { setBusy(""); }
            }}>{String(action.label || name)}</button>;
          })}
        </article>)}
  </section>;
}
