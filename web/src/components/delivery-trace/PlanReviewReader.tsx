import { useEffect, useMemo, useState } from "react";
import type { PlanPreview } from "../../api/types";
import { MarkdownText } from "../agent-session/MarkdownText";
import PlanMarkdownDocument from "./PlanMarkdownDocument";
import { buildPlanReview, object, planArtifactUrl, reviewPorts, rows, strings, text } from "./planReviewModel";
import type { ReviewArtifacts } from "./planReviewModel";

type View = "overview" | "modules" | "scenarios" | "original";
const views: [View, string][] = [["overview", "审批概览"], ["modules", "模块规格"], ["scenarios", "验收场景"], ["original", "完整原文"]];

export default function PlanReviewReader({ preview, projectId }: { preview: PlanPreview; projectId: string }) {
  const available = useMemo(() => reviewPorts.filter(name => preview.artifacts?.some(p => p.name === name)), [preview.artifacts]);
  const [view, setView] = useState<View>(available.includes("task_map") ? "overview" : "original");
  const [artifacts, setArtifacts] = useState<ReviewArtifacts>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(available.length > 0);
  const [selection, setSelection] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setArtifacts({}); setErrors([]); setLoading(available.length > 0);
    void Promise.all(available.map(async (name) => {
      try {
        // The API resolves only this Plan's bound ports and checks their digest.
        const response = await fetch(planArtifactUrl(projectId, preview.plan_id, name), { signal: controller.signal, headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const value: unknown = await response.json();
        if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("附件不是对象");
        return { name, value };
      } catch (error) {
        return { name, error: error instanceof Error ? error.message : "读取失败" };
      }
    })).then(results => {
      if (controller.signal.aborted) return;
      const next: ReviewArtifacts = {};
      const failures: string[] = [];
      results.forEach(result => {
        if ("error" in result) failures.push(`${result.name}：${result.error}`);
        else next[result.name] = result.value;
      });
      setArtifacts(next); setErrors(failures); setLoading(false);
    });
    return () => controller.abort();
  }, [available, projectId, preview.plan_id]);
  const model = useMemo(() => buildPlanReview(artifacts), [artifacts]);
  const selected = model.modules.find(m => m.id === selection) || model.modules[0];
  return <div className="plan-review">
    <nav className="plan-document-toolbar" aria-label="计划审阅视图">
      {views.map(([id, label]) => <button key={id} className="delivery-action-button" type="button" aria-pressed={view === id} onClick={() => setView(id)}>{label}</button>)}
    </nav>
    {view === "original" ? <PlanMarkdownDocument content={preview.markdown || ""} reviewContent={preview.review_dossier_markdown || ""} /> : <div className="plan-review-scroll" role="region" aria-label="计划审阅内容" tabIndex={0}>
      <p className="muted">以下内容来自本次 Plan 的绑定附件；规划条目与场景不代表实现或测试已通过。</p>
      {loading && <p role="status">正在读取同版规格…</p>}
      {errors.map(error => <p role="alert" key={error}>附件读取失败：{error}。请查看完整原文。</p>)}
      {!loading && model.warnings.map(warning => <p role="status" key={warning}>{warning}</p>)}
      {!loading && view === "overview" && <>
        <h2>本次交付与审批范围</h2>
        <p>{model.modules.length} 个任务 · {model.criterionCount} 个验收项 · {model.pageCount ?? "未提供"} 个页面 · {model.formCount ?? "未提供"} 类表单</p>
        {model.overview ? <MarkdownText content={model.overview} /> : <p>本版尚未提供独立审批摘要，以下保留任务合同中的目标；条件范围请结合待决事项及完整原文确认。</p>}
        {model.goals.map((goal, i) => <p key={i}><strong>{text(goal.id)}</strong> {text(goal.statement)}</p>)}
        <h3>待决事项与进入条件</h3>
        <DecisionList decisions={model.decisions} />
        <h3>业务旅程</h3>
        {model.journeys ? <MarkdownText content={model.journeys} /> : <p>本版尚未提供独立业务流程说明，请结合模块规格和完整原文审阅。</p>}
      </>}
      {!loading && (view === "modules" || view === "scenarios") && <>
        <label className="plan-review-selector">任务 / 模块
          <select aria-label="审阅模块" value={selected?.id || ""} onChange={event => setSelection(event.target.value)}>
            {model.modules.map(module => <option key={module.id} value={module.id}>{module.id} · {module.title}</option>)}
          </select>
        </label>
        {selected && <>
          <h2>{selected.title || selected.id}</h2>
          <p>{selected.id} · 前置：{selected.dependencies.join("、") || "未声明"}</p>
          <SourceRefs refs={selected.sourceRefs} />
          {view === "modules" ? <>
            {selected.specification ? <MarkdownText content={selected.specification} /> : <>
              <p role="status">本版未细化独立模块规格，以下显示已绑定任务描述和验收项；字段、权限及异常细则需对照来源。</p>
              <p>{selected.description}</p>
            </>}
            <h3>页面与表单</h3>
            <p>{[...selected.pages, ...selected.forms].map(p => `${text(p.id)} ${text(p.title)}`).join("；") || "未提供页面映射"}</p>
            <h3>相关决定</h3><DecisionList decisions={selected.decisions} />
          </> : <>
            <h3>具体验收场景（计划，未执行）</h3>
            {!selected.scenarios.length && <p role="status">本版尚未提供逐条 Given / When / Then 场景；下方验收项不能代替测试执行证据。</p>}
            {selected.scenarios.map((scenario, i) => <article className="plan-review-card" key={i}>
              <h4>{scenario.id} {scenario.title}</h4>
              <dl><dt>前置 Given</dt><dd>{scenario.given || "待补充"}</dd><dt>操作 When</dt><dd>{scenario.when || "待补充"}</dd><dt>结果 Then</dt><dd>{scenario.then || "待补充"}</dd></dl>
              <p>验收项：{scenario.acceptanceIds.join("、") || "未绑定"} · 命令：{scenario.commandIds.join("、") || "未绑定"}</p>
              <SourceRefs refs={scenario.sourceRefs} />
              {scenario.issues.map(issue => <p role="status" key={issue}>{issue}</p>)}
            </article>)}
          </>}
          <h3>验收项与验证责任</h3>
          {!selected.criteria.length && <p>未提供结构化验收项，请查看完整原文。</p>}
          {selected.criteria.map((ac, i) => <article className="plan-review-card" key={i}>
            <h4>{ac.id}</h4><p>{ac.statement}</p>
            <p>验证层级：{ac.tier || "未声明"} · 命令：{ac.commandIds.join("、") || "未绑定"}</p>
            <SourceRefs refs={ac.sourceRef ? [ac.sourceRef] : []} />
          </article>)}
        </>}
      </>}
    </div>}
  </div>;
}

function SourceRefs({ refs }: { refs: string[] }) {
  return refs.length ? <details className="plan-review-sources"><summary>来源条款（{refs.length}）</summary>{refs.map((ref, i) => <p key={i}><code>{ref}</code></p>)}</details> : <p className="muted">未提供来源条款。</p>;
}

function DecisionList({ decisions }: { decisions: Record<string, unknown>[] }) {
  if (!decisions.length) return <p>未列出相关决定；不代表所有问题均已关闭。</p>;
  return <>{decisions.map((decision, i) => <details className="plan-review-card" key={i}>
    <summary>{text(decision.decision_id)} · {text(decision.status) || "状态未声明"} · {text(decision.question)}</summary>
    <p>负责人：{text(decision.named_owner) || "待指定"} · {text(decision.responsible_role)}</p>
    <p>最迟确认：{text(decision.enter_before) || "未声明"} · 影响任务：{strings(decision.affected_task_ids).join("、") || text(decision.producer_task_id) || "未声明"}</p>
    <p>关闭条件：{text(decision.done_when) || "未声明"}</p>
    <p>{text(decision.pending_boundary)} {text(decision.approval_scope)}</p>
    {rows(decision.options).map((option, index) => <p key={index}>{text(object(option).label)}</p>)}
    <SourceRefs refs={strings(decision.evidence_refs)} />
  </details>)}</>;
}
