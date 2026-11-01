import { useEffect, useMemo, useState } from "react";
import type { PlanPreview } from "../../api/types";
import { MarkdownText } from "../agent-session/MarkdownText";
import PlanMarkdownDocument from "./PlanMarkdownDocument";
import PlanSourceReader from "./PlanSourceReader";
import PlanTraceReader from "./PlanTraceReader";
import { DecisionList, PlanSourceContext, SourceRefs } from "./PlanReviewDetails";
import { buildPlanReview, relatedTasks, strings, text } from "./planReviewModel";
import { usePlanArtifacts } from "./usePlanArtifacts";
import "./planReader.css";

type View = "overview" | "modules" | "pages" | "scenarios" | "decisions" | "coverage" | "original" | "sources";
const views: [View, string][] = [["overview", "审批概览"], ["modules", "模块规格"], ["pages", "页面与设计"], ["scenarios", "验收场景"], ["decisions", "待决事项"], ["coverage", "覆盖审查"], ["original", "完整原文"], ["sources", "来源原文"]];

export default function PlanReviewReader({ preview, projectId }: { preview: PlanPreview; projectId: string }) {
  const readLocation = () => {
    const params = new URLSearchParams(window.location.hash.slice(1));
    const samePlan = params.get("plan") === preview.plan_id;
    const candidate = samePlan ? params.get("view") : "";
    const view = views.some(([id]) => id === candidate) ? candidate as View : preview.artifacts?.some(p => p.name === "task_map") ? "overview" : "original";
    return { view: view as View, task: samePlan ? params.get("task") || "" : "", source: samePlan ? params.get("source") || "" : "", anchor: samePlan ? params.get("anchor") || "" : "" };
  };
  const [location, setLocation] = useState(readLocation);
  const [query, setQuery] = useState("");
  const { view } = location;
  const navigate = (next: Partial<typeof location>) => {
    const value = { ...location, ...next };
    setLocation(value);
    const url = new URL(window.location.href);
    if (url.searchParams.get("page") === "inbox") url.searchParams.set("plan", preview.plan_id);
    url.hash = new URLSearchParams({ plan: preview.plan_id, ...value }).toString();
    window.history.replaceState(window.history.state, "", url);
  };
  useEffect(() => { const update = () => setLocation(readLocation()); window.addEventListener("hashchange", update); window.addEventListener("popstate", update); return () => { window.removeEventListener("hashchange", update); window.removeEventListener("popstate", update); }; }, [preview.plan_id]);
  const detailed = view === "coverage" || view === "scenarios";
  const { artifacts, errors, loading } = usePlanArtifacts(preview, projectId, detailed);
  const model = useMemo(() => buildPlanReview(artifacts), [artifacts]);
  const selected = location.task ? model.modules.find(m => m.id === location.task) : model.modules[0];
  const decisions = preview.decision_review?.available ? preview.decision_review.items : model.decisions;
  const counts = preview.decision_review?.available ? preview.decision_review.counts : null;
  const decisionSummary = counts ? `待决定 ${counts.pending} · 明确阻塞 ${counts.blocking} · 已授权待落实 ${counts.follow_up} · 已关闭 ${counts.resolved} · 其他状态 ${counts.unknown}` : "完整决定登记状态未确认，请核对同版工件。";
  const filteredModules = model.modules.filter(m => `${m.id} ${m.title} ${m.description}`.toLowerCase().includes(query.toLowerCase()));
  const openSource = (source: string, anchor: string) => navigate({ view: "sources", source, anchor });
  const selectTask = (task: string) => navigate({ view: "modules", task });
  const sourceProps = { preview, projectId };
  return <PlanSourceContext.Provider value={openSource}><div className="plan-review">
    <div className="plan-product-layout">
      <nav className="plan-product-nav" aria-label="计划审阅视图">
        <p className="plan-product-eyebrow">产品文档</p>
        {views.map(([id, label]) => <button key={id} className="delivery-action-button" type="button" aria-pressed={view === id} onClick={() => navigate({ view: id })}>{label}</button>)}
        {(view === "modules" || view === "scenarios") && <>
          <label>查找模块<input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label>
          <div className="plan-module-links">{filteredModules.map(module => <button type="button" key={module.id} aria-current={selected?.id === module.id ? "true" : undefined} onClick={() => navigate({ task: module.id })}>{module.title || module.id}<small>{module.id}</small></button>)}</div>
        </>}
      </nav>
      <div className="plan-product-content">
        {view === "original" ? <PlanMarkdownDocument content={preview.markdown || ""} reviewContent={preview.review_dossier_markdown || ""} initialAnchor={location.anchor} onAnchorChange={anchor => navigate({ anchor })} />
          : view === "sources" ? <PlanSourceReader preview={preview} projectId={projectId} name={location.source} anchor={location.anchor} onSelect={openSource} />
          : <div className="plan-review-scroll" role="region" aria-label="计划审阅内容" tabIndex={0}>
          <p className="muted">版本 {preview.plan_id} · {preview.status} · 以下为规划快照，计划分配与场景不代表实现或测试已通过。</p>
          {loading && <p role="status">正在读取同版规格…</p>}
          {errors.map(error => <p role="alert" key={error}>附件读取失败：{error}。请查看完整原文。</p>)}
          {preview.decision_review?.error && <p role="alert">{preview.decision_review.error}</p>}
          {!loading && model.warnings.map(warning => <p role="status" key={warning}>{warning}</p>)}
          {!loading && view === "overview" && <>
            <h2>本次交付与审批范围</h2>
            <p>{model.modules.length} 个任务 · {model.criterionCount} 个验收项 · {model.pageCount ?? "未提供"} 个页面 · {model.formCount ?? "未提供"} 类表单</p>
            {model.overview ? <MarkdownText key={`overview:${model.overview}`} content={model.overview} /> : <p>本版尚未提供独立产品摘要，请结合完整原文确认范围。</p>}
            <h3>业务旅程</h3>
            {model.journeys ? <MarkdownText key={`journeys:${model.journeys}`} content={model.journeys} /> : <p>本版尚未提供独立业务流程说明，请结合模块规格和完整原文审阅。</p>}
            <details className="plan-review-card"><summary>完整交付目标（含工程约束）</summary>{model.goals.map((goal, i) => <p key={i}><strong>{text(goal.id)}</strong> {text(goal.statement)}</p>)}</details>
            <h3>业务与交付模块</h3>
            {model.groups.length > 0 ? model.groups.map((group, i) => <article className="plan-review-card" key={i}><h4>{text(group.title || group.name || group.module_id || group.id)}</h4><p>{text(group.description || group.summary)}</p>{strings(group.task_ids).map(id => <button className="delivery-action-button" type="button" key={id} onClick={() => selectTask(id)}>{model.modules.find(m => m.id === id)?.title || id}</button>)}</article>) : <p>本版未提供业务分组，可从模块规格查看各任务的用户行为。</p>}
            <h3>待决事项与进入条件</h3><p>{decisionSummary}</p><DecisionList decisions={decisions} {...sourceProps} />
          </>}
          {!loading && view === "decisions" && <>
            <h2>待决事项与阶段边界</h2>
            <p>共 {decisions.length} 条登记。未来待决定、已授权待落实与当前阻塞分别保留；批准 Plan 不会自动关闭它们。</p>
            <p>{decisionSummary}</p>
            <DecisionList decisions={decisions} {...sourceProps} />
          </>}
          {!loading && view === "pages" && <>
            <h2>页面与设计</h2><p>参考素材仅表示来源；设计、实现与视觉验收使用本版工件的原始状态。</p>
            <label>查找页面或表单<input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label>
            {[...model.pages, ...model.forms].filter(p => `${text(p.id)} ${text(p.title)}`.toLowerCase().includes(query.toLowerCase())).map((p, i) => <article className="plan-review-card" key={i}>
              <h3>{text(p.id)} · {text(p.title)}</h3>
              <p>设计：{text(p.design_status) || "未声明"} · 实现：{text(p.implementation_status) || "未声明"} · 视觉验收：{text(p.visual_acceptance_status) || "未声明"}</p>
              <p>参考素材：{strings(p.reference_images).join("、") || "未绑定参考图"} · {text(p.reference_status)}</p>
              <p>{text(p.condition)} {text(p.disposition)}</p>
              {relatedTasks(p).map(id => <button type="button" className="delivery-action-button" key={id} onClick={() => selectTask(id)}>{model.modules.find(m => m.id === id)?.title || id}</button>)}
              <SourceRefs refs={[text(p.source_ref)].filter(Boolean)} {...sourceProps} />
            </article>)}
          </>}
          {!loading && view === "coverage" && <PlanTraceReader artifacts={artifacts} preview={preview} projectId={projectId} onTask={selectTask} />}
          {!loading && (view === "modules" || view === "scenarios") && <>
            {location.task && !selected && <p role="alert">本版未找到任务 {location.task}，请选择已绑定任务。</p>}
            <label className="plan-review-selector">任务 / 模块<select aria-label="审阅模块" value={selected?.id || ""} onChange={event => navigate({ task: event.target.value })}>
              {!selected && <option value="">请选择任务</option>}
              {model.modules.map(module => <option key={module.id} value={module.id}>{module.id} · {module.title}</option>)}
            </select></label>
            {selected && <section key={selected.id}>
              <h2>{selected.title || selected.id}</h2><p>{selected.id} · 前置：{selected.dependencies.join("、") || "未声明"}</p>
              <SourceRefs refs={selected.sourceRefs} {...sourceProps} />
              {view === "modules" ? <>
                {selected.specification ? <MarkdownText key={`${selected.id}:${selected.specification}`} content={selected.specification} /> : <><p role="status">本版未细化独立模块规格，以下显示已绑定任务描述和验收项；字段、权限及异常细则需对照来源。</p><p>{selected.description}</p></>}
                <h3>页面与表单</h3><p>{[...selected.pages, ...selected.forms].map(p => `${text(p.id)} ${text(p.title)}`).join("；") || "未提供页面映射"}</p>
                <h3>相关决定</h3><DecisionList decisions={selected.decisions} {...sourceProps} />
              </> : <>
                <h3>具体验收场景（计划，未执行）</h3>
                {!selected.scenarios.length && <p role="status">本版尚未提供逐条 Given / When / Then 场景；下方验收项不能代替测试执行证据。</p>}
                {selected.scenarios.map((scenario, i) => <article className="plan-review-card" key={i}>
                  <h4>{scenario.id} {scenario.title || "未命名场景"}</h4>
                  <dl><dt>前置 Given</dt><dd>{scenario.given || "待补充"}</dd><dt>操作 When</dt><dd>{scenario.when || "待补充"}</dd><dt>结果 Then</dt><dd>{scenario.then || "待补充"}</dd></dl>
                  <p>验收项：{scenario.acceptanceIds.join("、") || "未绑定"} · 命令：{scenario.commandIds.join("、") || "未绑定"}</p>
                  <SourceRefs refs={scenario.sourceRefs} {...sourceProps} />
                  {scenario.issues.map(issue => <p role="status" key={issue}>{issue}</p>)}
                </article>)}
                <h3>正式验收矩阵</h3>
                {!selected.formalScenarios.length && <p>未提供本模块的正式验收场景。</p>}
                {selected.formalScenarios.map((scenario, i) => <article className="plan-review-card" key={i}><h4>{text(scenario.id)} · {text(scenario.trigger || scenario.title)}</h4><p>{text(scenario.expected)}</p><p>AC：{text(scenario.acceptance_id)} · 命令：{strings(scenario.command_ids).join("、") || "未绑定"}</p><p>{text(scenario.verification_owner)} / {text(scenario.verification_tier)} · {text(scenario.pass_rule)}</p></article>)}
              </>}
              <h3>验收项与验证责任</h3>
              {!selected.criteria.length && <p>未提供结构化验收项，请查看完整原文。</p>}
              {selected.criteria.map((ac, i) => <article className="plan-review-card" key={i}><h4>{ac.id}</h4><p>{ac.statement}</p><p>验证层级：{ac.tier || "未声明"} · 命令：{ac.commandIds.join("、") || "未绑定"}</p><SourceRefs refs={ac.sourceRef ? [ac.sourceRef] : []} {...sourceProps} /></article>)}
            </section>}
          </>}
        </div>}
      </div>
    </div>
  </div></PlanSourceContext.Provider>;
}
