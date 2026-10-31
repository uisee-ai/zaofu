import { useEffect, useMemo, useRef, useState } from "react";
import type { PlanPreview } from "../../api/types";
import { buildPlanTrace, comparePlanTrace, dispositionLabel, traceSearch } from "./planTraceModel";
import { object, planArtifactUrl, text } from "./planReviewModel";
import type { ReviewArtifacts, ReviewPort } from "./planReviewModel";
import { SourceRefs } from "./PlanReviewDetails";

export default function PlanTraceReader({ artifacts, preview, projectId, onTask }: {
  artifacts: ReviewArtifacts; preview: PlanPreview; projectId: string; onTask: (id: string) => void;
}) {
  const trace = useMemo(() => buildPlanTrace(artifacts), [artifacts]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(0);
  const [versionId, setVersionId] = useState("");
  const [comparison, setComparison] = useState<{ id: string; rows: ReturnType<typeof comparePlanTrace> } | null>(null);
  const [comparisonPage, setComparisonPage] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController>();
  useEffect(() => () => request.current?.abort(), []);
  const filtered = useMemo(() => trace.filter(row => traceSearch(row).includes(query.trim().toLowerCase()) && (
    filter === "all" || filter === "unmapped" && row.disposition === "unmapped" ||
    filter === "pending" && row.disposition === "pending_decision" || filter === "invalid" && row.issues.length > 0 ||
    filter === "unverified" && ["NOT_RUN", "BLOCKED", "未提供", "UNVERIFIED"].includes(row.evidenceStatus)
  )), [trace, query, filter]);
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 25) - 1));
  const compare = async () => {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setComparison(null); setComparisonPage(0); setError(""); setBusy(true);
    try {
      const id = versionId.trim();
      if (!id || id === preview.plan_id) throw new Error("请输入另一个 Plan ID");
      for (const [name, field] of [["source_index", "traceability"], ["coverage_ledger", "entries"]] as const) {
        const port = object(artifacts[name]);
        if (!Array.isArray(port[field] ?? port.rows)) throw new Error(`当前版本未读取有效 ${name}`);
      }
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(id)}/preview`, { signal: controller.signal });
      if (!response.ok) throw new Error(`版本读取失败：HTTP ${response.status}`);
      const old: PlanPreview = await response.json();
      if (!old.ok || !(old.trace_id && old.trace_id === preview.trace_id || old.pdd_id && old.pdd_id === preview.pdd_id)) throw new Error("无法确认两个 Plan 属于同一需求或流程，未进行比较");
      const values: ReviewArtifacts = {};
      await Promise.all((["source_index", "coverage_ledger"] as ReviewPort[]).map(async name => {
        if (!old.artifacts?.some(p => p.name === name)) throw new Error(`对比版本未绑定 ${name}`);
        const res = await fetch(planArtifactUrl(projectId, old.plan_id, name), { signal: controller.signal });
        if (!res.ok) throw new Error(`${name}：HTTP ${res.status}`);
        const value: unknown = await res.json();
        const port = object(value);
        const field = name === "source_index" ? "traceability" : "entries";
        if (!Array.isArray(port[field] ?? port.rows)) throw new Error(`${name}：来源/覆盖列表缺失`);
        values[name] = value;
      }));
      if (!controller.signal.aborted) setComparison({ id: old.plan_id, rows: comparePlanTrace(buildPlanTrace(values), trace) });
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "版本读取失败"); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <section aria-label="来源覆盖审查">
    <h2>来源覆盖审查</h2>
    <p>已分配表示计划去向；结构引用齐全不代表语义正确或测试通过。证据状态来自本版工件，实时执行结果请到任务查看。</p>
    <p>{trace.length} 条来源记录 · {trace.filter(r => r.disposition === "unmapped").length} 条未分配 · {trace.filter(r => r.disposition === "pending_decision").length} 条待决定 · {trace.filter(r => r.issues.length).length} 条需核对引用</p>
    {!artifacts.source_index && <p role="alert">未绑定或未读取独立来源索引，不能判断范围完整性。</p>}
    <div className="plan-review-filters">
      <label>搜索条款、任务或验收项<input type="search" value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} /></label>
      <label>覆盖状态<select aria-label="覆盖状态" value={filter} onChange={e => { setFilter(e.target.value); setPage(0); }}>
        <option value="all">全部</option><option value="unmapped">未分配</option><option value="pending">待决定</option><option value="invalid">引用缺口</option><option value="unverified">未验证 / 阻塞</option>
      </select></label>
    </div>
    <p role="status">匹配 {filtered.length} 条 · 第 {currentPage + 1} / {Math.max(1, Math.ceil(filtered.length / 25))} 页</p>
    <div className="plan-trace-list">
      {filtered.slice(currentPage * 25, currentPage * 25 + 25).map(row => <details key={row.id} className="plan-review-card">
        <summary><strong>{row.id}</strong> · {dispositionLabel(row.disposition)} · {row.statement.slice(0, 140) || "来源正文未提供"}</summary>
        <p>{row.statement}</p><SourceRefs refs={row.sourceRefs} preview={preview} projectId={projectId} />
        <h3>任务规格</h3>
        {row.taskIds.map(id => <button type="button" className="delivery-action-button" key={id} onClick={() => onTask(id)}>{id}</button>)}
        {row.tasks.map((task, i) => <p key={i}>{text(task.title)}：{text(task.description || task.source_excerpt)}</p>)}
        <h3>验收与命令</h3>
        {row.criteria.map((ac, i) => <p key={i}><strong>{text(ac.id)}</strong> {text(ac.statement)}</p>)}
        <p>AC：{row.acceptanceIds.join("、") || "未绑定"}</p>
        <p>命令：{row.commandIds.join("、") || "未绑定"}</p>
        {row.commands.map((command, i) => <p key={i}><code>{text(command.command)}</code> · {text(command.owner)} / {text(command.tier)}</p>)}
        <p>工件声明的证据状态：{row.evidenceStatus}（此阅读器不裁决通过）</p>
        <p>以下位置来自计划声明，不证明文件已生成或通过独立验证。</p>
        <SourceRefs label="证据位置" refs={row.evidence} preview={preview} projectId={projectId} />
        <p>{row.reason}</p>{row.issues.map(issue => <p role="status" key={issue}>{issue}</p>)}
      </details>)}
    </div>
    <div className="plan-document-pagination">
      <button type="button" className="delivery-action-button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一组条款</button>
      <button type="button" className="delivery-action-button" disabled={(currentPage + 1) * 25 >= filtered.length} onClick={() => setPage(currentPage + 1)}>下一组条款</button>
    </div>
    <details className="plan-review-card"><summary>比较 Plan 版本</summary>
      <p>比较来源正文与任务 / AC / 命令分配，不比较实时产品完成状态。当前版本：{preview.plan_id}</p>
      <label>对比 Plan ID<input value={versionId} onChange={e => { request.current?.abort(); setBusy(false); setVersionId(e.target.value); setComparison(null); setError(""); }} /></label>
      <button type="button" className="delivery-action-button" disabled={busy} onClick={() => void compare()}>{busy ? "读取中…" : "比较版本"}</button>
      {error && <p role="alert">{error}</p>}
      {comparison && <><p>{comparison.id} → {preview.plan_id}：{comparison.rows.length} 条变化</p>
        {comparison.rows.slice(comparisonPage * 25, comparisonPage * 25 + 25).map(change => <details key={change.id}><summary>{change.kind} · {change.id}</summary>
          <p>原文：{change.before?.statement || "不存在"}</p><p>新版：{change.after?.statement || "不存在"}</p>
          <p>任务：{change.before?.taskIds.join("、") || "无"} → {change.after?.taskIds.join("、") || "无"}</p>
          <p>状态：{dispositionLabel(change.before?.disposition || "")} → {dispositionLabel(change.after?.disposition || "")}</p>
          <p>AC：{change.before?.acceptanceIds.join("、") || "无"} → {change.after?.acceptanceIds.join("、") || "无"}</p>
          <p>命令：{change.before?.commandIds.join("、") || "无"} → {change.after?.commandIds.join("、") || "无"}</p>
        </details>)}
        <div className="plan-document-pagination">
          <button type="button" className="delivery-action-button" disabled={!comparisonPage} onClick={() => setComparisonPage(comparisonPage - 1)}>上一组变化</button>
          <span>第 {comparisonPage + 1} / {Math.max(1, Math.ceil(comparison.rows.length / 25))} 页</span>
          <button type="button" className="delivery-action-button" disabled={(comparisonPage + 1) * 25 >= comparison.rows.length} onClick={() => setComparisonPage(comparisonPage + 1)}>下一组变化</button>
        </div>
      </>}
    </details>
  </section>;
}
