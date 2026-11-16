import { useEffect, useMemo, useState } from "react";
import type { PlanPreview } from "../../api/types";
import { MarkdownText } from "../agent-session/MarkdownText";
import PlanMarkdownDocument from "./PlanMarkdownDocument";
import PlanApprovalOverview from "./PlanApprovalOverview";
import PlanSourceReader from "./PlanSourceReader";
import PlanTraceReader from "./PlanTraceReader";
import PlanInputReviewPanel from "./PlanInputReviewPanel";
import { DecisionList, PlanSourceContext, SourceRefs } from "./PlanReviewDetails";
import { buildPlanReview, decisionStatusDistribution, relatedTasks, strings, text } from "./planReviewModel";
import { usePlanArtifacts } from "./usePlanArtifacts";
import { planReadingSections, planTaskSpecifications, type ReadingSection } from "./planReadingSections";
import { decisionReadingSummary } from "./planDecisionView";
import "./planReader.css";

type View = "inputs" | "overview" | "modules" | "pages" | "scenarios" | "decisions" | "coverage" | "changes" | "original" | "sources";
const views: [View, string][] = [["original", "完整原文"], ["overview", "审批概览"], ["changes", "变更审阅"], ["modules", "模块规格"], ["pages", "页面与设计"], ["scenarios", "验收场景"], ["decisions", "待决事项"], ["coverage", "覆盖审查"], ["inputs", "任务输入"], ["sources", "来源原文"]];

export default function PlanReviewReader({ preview, projectId }: { preview: PlanPreview; projectId: string }) {
  const readLocation = () => {
    const params = new URLSearchParams(window.location.hash.slice(1));
    const samePlan = params.get("plan") === preview.plan_id;
    const candidate = samePlan ? params.get("view") : "";
    const view = views.some(([id]) => id === candidate) ? candidate as View : "original";
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
    const hash = new URLSearchParams({ plan: preview.plan_id, ...value });
    if (new URLSearchParams(url.hash.slice(1)).get("reader") === "focus") hash.set("reader", "focus");
    url.hash = hash.toString();
    window.history.replaceState(window.history.state, "", url);
  };
  useEffect(() => { const update = () => setLocation(readLocation()); window.addEventListener("hashchange", update); window.addEventListener("popstate", update); return () => { window.removeEventListener("hashchange", update); window.removeEventListener("popstate", update); }; }, [preview.plan_id]);
  const detailed = view === "coverage" || view === "scenarios";
  const { artifacts, errors, loading } = usePlanArtifacts(preview, projectId, detailed);
  const model = useMemo(() => buildPlanReview(artifacts), [artifacts]);
  const excerpts = useMemo(() => planReadingSections(preview.markdown || ""), [preview.markdown]);
  const specifications = useMemo(() => planTaskSpecifications(preview.markdown || "", model.modules.map(m => m.id)), [preview.markdown, model]);
  const selected = location.task ? model.modules.find(m => m.id === location.task) : model.modules[0];
  const decisions = preview.decision_review?.available ? preview.decision_review.items : model.decisions;
  const registerKnown = preview.decision_review ? preview.decision_review.available : Array.isArray((artifacts.task_map as Record<string, unknown> | undefined)?.owner_decision_items);
  const decisionSummary = registerKnown ? decisionReadingSummary(decisions) : "完整决定登记状态未确认，请核对同版工件。";
  const filteredModules = model.modules.filter(m => `${m.id} ${m.title} ${m.description}`.toLowerCase().includes(query.toLowerCase()));
  const openSource = (source: string, anchor: string) => navigate({ view: "sources", source, anchor });
  const selectTask = (task: string) => navigate({ view: "modules", task });
  const sourceProps = { preview, projectId };
  const quoted = (sections: ReadingSection[]) => sections.length ? sections.map(section => <section className="plan-original-excerpt" key={section.key}>
    <p className="muted">同版原文章节 · 保留作者表述</p>
    <MarkdownText content={section.content} />
    <button type="button" className="delivery-action-button" onClick={() => navigate({ view: "original", anchor: section.anchor })}>在完整原文中阅读：{section.title}</button>
  </section>) : <p>该视图尚未映射内容。<button type="button" className="delivery-action-button" onClick={() => navigate({ view: "original" })}>查看同版完整原文</button></p>;
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
        {view === "original" ? <PlanMarkdownDocument statusLine={`版本 ${preview.plan_id} · ${preview.status} · 规划快照，尚不代表实施或测试通过`} content={preview.markdown || ""} reviewContent={preview.review_dossier_markdown || ""} initialAnchor={location.anchor} onAnchorChange={anchor => navigate({ anchor })} />
          : view === "sources" ? <PlanSourceReader preview={preview} projectId={projectId} name={location.source} anchor={location.anchor} onSelect={openSource} />
          : <div className="plan-review-scroll" role="region" aria-label="计划审阅内容" tabIndex={0}>
          <p className="muted">{preview.status === "pending" ? "待审批" : preview.status} · 以下为规划快照，计划分配与场景不代表实现或测试已通过。</p>
          {loading && <p role="status">正在读取同版规格…</p>}
          {errors.map(error => <p role="alert" key={error}>附件读取失败：{error}。请查看完整原文。</p>)}
          {preview.decision_review?.error && <p role="alert">{preview.decision_review.error}</p>}
          {!loading && model.warnings.map(warning => <p role="status" key={warning}>{warning}</p>)}
          {!loading && view === "modules" && specifications.warnings.map(warning => <p role="status" key={warning}>{warning}</p>)}
          {!loading && view === "overview" && <PlanApprovalOverview
            model={model} excerpts={excerpts} decisionSummary={decisionSummary} decisions={decisions}
            preview={preview} projectId={projectId} quoted={quoted} selectTask={selectTask}
            onChanges={() => navigate({ view: "changes" })} onDecisions={() => navigate({ view: "decisions" })} />}
          {!loading && view === "changes" && <>
            <h2>本次变更与保留义务</h2>
            <p>以下引用作者的同版变更说明；未提供不代表没有变更。历史版本逐项差异可在覆盖审查中比较。</p>
            {model.changes ? <MarkdownText content={model.changes} /> : quoted(excerpts.changes)}
            <button type="button" className="delivery-action-button" onClick={() => navigate({ view: "coverage" })}>比较绑定版本与来源影响</button>
          </>}
          {!loading && view === "decisions" && <>
            <h2>待决事项与阶段边界</h2>
            <p>{registerKnown ? `共 ${decisions.length} 条登记。` : "结构化登记未确认，数量未知；请核对下方同版原文。"}未来待决定、已授权待落实与当前阻塞分别保留；批准 Plan 不会自动关闭它们。</p>
            <p>{decisionSummary}</p>
            {registerKnown && <p>原始状态：{decisionStatusDistribution(decisions) || "未登记"}。保留作者状态，不据此推定关闭或执行授权。</p>}
            <DecisionList decisions={decisions} {...sourceProps} />
            {excerpts.decisions.length > 0 && <details className="plan-review-card" open={!decisions.length}>
              <summary>同版决定原文</summary>
              <p>以下保留正文中的决定；结构化登记与正文需要逐项对账，空登记不表示正文没有决定。这里不推断决定状态或执行授权。</p>
              {quoted(excerpts.decisions)}
            </details>}
          </>}
          {!loading && view === "pages" && <>
            <h2>页面与设计</h2><p>参考素材仅表示来源；设计、实现与视觉验收使用本版工件的原始状态。</p>
            {quoted(excerpts.pages)}
            <label>查找页面或表单<input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label>
            {[...model.pages, ...model.forms].filter(p => `${text(p.id || p.page_id || p.form_id)} ${text(p.title || p.name)}`.toLowerCase().includes(query.toLowerCase())).map((p, i) => <article className="plan-review-card" key={i}>
              <h3>{[text(p.id || p.page_id || p.form_id), text(p.title || p.name)].filter(Boolean).join(" · ") || `页面 / 表单 ${i + 1}`}</h3>
              <p>设计：{text(p.design_status) || "未声明"} · 实现：{text(p.implementation_status) || "未声明"} · 视觉验收：{text(p.visual_acceptance_status) || "未声明"}</p>
              <p>参考素材：{strings(p.reference_images).join("、") || "未绑定参考图"} · {text(p.reference_status)}</p>
              <p>{text(p.condition)} {text(p.disposition)}</p>
              {relatedTasks(p).map(id => <button type="button" className="delivery-action-button" key={id} onClick={() => selectTask(id)}>{model.modules.find(m => m.id === id)?.title || id}</button>)}
              <SourceRefs refs={[text(p.source_ref)].filter(Boolean)} {...sourceProps} />
            </article>)}
          </>}
          {view === "inputs" && <PlanInputReviewPanel key={`${projectId}:${preview.plan_id}`} preview={preview} projectId={projectId} />}
          {!loading && view === "coverage" && <PlanTraceReader artifacts={artifacts} preview={preview} projectId={projectId} taskId={location.task} onClearTask={() => navigate({ task: "" })} onTask={selectTask} />}
          {!loading && (view === "modules" || view === "scenarios") && <>
            {location.task && !selected && <p role="alert">本版未找到任务 {location.task}，请选择已绑定任务。</p>}
            <label className="plan-review-selector">任务 / 模块<select aria-label="审阅模块" value={selected?.id || ""} onChange={event => navigate({ task: event.target.value })}>
              {!selected && <option value="">请选择任务</option>}
              {model.modules.map(module => <option key={module.id} value={module.id}>{module.id} · {module.title}</option>)}
            </select></label>
            {selected && <section key={selected.id}>
              <h2>{selected.title || selected.id}</h2><p>{selected.id} · 前置：{selected.dependencies.join("、") || "未声明"}</p>
              <button type="button" className="delivery-action-button" onClick={() => navigate({ view: "coverage", task: selected.id })}>查看本任务来源、验收与证据</button>
              <SourceRefs refs={selected.sourceRefs} {...sourceProps} />
              {view === "modules" ? <>
                <p className="muted">以下为作者规格与任务合同；内容存在不代表独立评审、执行授权或产品验证通过。</p>
                {specifications.shared.length > 0 && <details className="plan-review-card"><summary>同版共享合同</summary>{quoted(specifications.shared)}</details>}
                {selected.specification ? <><p className="muted">规格来源：同版 execution_spec 任务分区</p><MarkdownText key={`${selected.id}:${selected.specification}`} content={selected.specification} /></>
                  : specifications.tasks[selected.id] ? <section className="plan-original-excerpt">
                    <p className="muted">同版原文；与其他任务完全相同的章节可展开阅读，完整原文保持不变。</p>
                    {specifications.reading[selected.id].map(part => part.shared
                      ? <details className="plan-review-card" key={part.key}><summary>共享重复章节：{part.title}</summary><MarkdownText content={part.content} /></details>
                      : <MarkdownText key={part.key} content={part.content} />)}
                    <button type="button" className="delivery-action-button" onClick={() => navigate({ view: "original", anchor: specifications.tasks[selected.id].anchor })}>在完整原文中阅读：{specifications.tasks[selected.id].title}</button>
                  </section>
                  : <><p role="status">本版未细化独立模块规格，以下显示已绑定任务描述和验收项；字段、权限及异常细则需对照来源。</p><p>{selected.description}</p></>}
                {specifications.unassigned.length > 0 && <details className="plan-review-card">
                  <summary>任务规格之外的原文章节（需核对适用性）</summary>
                  <p>以下章节未按任务分区，可能包含准备、恢复或交接条件；展示不表示它们全部属于本任务。</p>
                  {specifications.unassigned.map(section => <details key={section.key}>
                    <summary>{section.title}</summary>{quoted([section])}
                  </details>)}
                </details>}
                <details className="plan-review-card"><summary>任务责任与验证命令</summary>
                  <p>实现责任：{selected.owner || "未声明"}</p>
                  <p>允许写入：{selected.allowedPaths.join("、") || "未声明，请核对工程附件"}</p>
                  {!selected.commandReferences.length && <p>尚未取得本任务的验证命令，请核对工程附件。</p>}
                  <p>按原始生产者和验证责任展示；实际执行阶段由运行时选择，引用最终命令不表示当前任务必须提前执行。</p>
                  {selected.commandReferences.map(ref => <article key={ref.id}><h4>{ref.id} · {ref.label}</h4>{ref.definitions.length === 1 && <><pre className="plan-task-command" tabIndex={0} aria-label={`验证命令 ${ref.id}`}><code>{text(ref.definitions[0].command)}</code></pre><p>{text(ref.definitions[0].owner)} · {text(ref.definitions[0].tier)} · AC：{strings(ref.definitions[0].acceptance_ids).join("、") || "未绑定"}</p></>}</article>)}
                </details>
                <h3>页面与表单</h3><p>{[...selected.pages, ...selected.forms].map(p => `${text(p.id || p.page_id || p.form_id)} ${text(p.title || p.name)}`).join("；") || "未提供页面映射"}</p>
                <h3>相关决定</h3><DecisionList decisions={selected.decisions} {...sourceProps} />
              </> : <>
                <h3>具体验收场景（计划，未执行）</h3>
                {!selected.scenarios.length && <p role="status">该模块尚未映射逐条 Given / When / Then 场景，请核对同版原文；下方验收项不能代替测试执行证据。</p>}
                {selected.scenarios.map((scenario, i) => <article className="plan-review-card" key={i}>
                  <h4>{scenario.id} {scenario.title}</h4><p className="muted">证据状态：{scenario.evidenceStatus}</p>
                  <dl><dt>前置 Given</dt><dd>{scenario.given || "待补充"}</dd><dt>操作 When</dt><dd>{scenario.when || "待补充"}</dd><dt>结果 Then</dt><dd>{scenario.then || "待补充"}</dd></dl>
                  <p>验收项：{scenario.acceptanceIds.join("、") || "未绑定"} · 命令：{scenario.commandIds.join("、") || "未绑定"}</p>
                  <details><summary>逐任务验收责任</summary>
                    {scenario.responsibilities.map(responsibility => <section key={responsibility.taskId}>
                      <h5>{responsibility.taskId}{responsibility.taskId === selected.id ? "（当前任务）" : ""}</h5>
                      <p>直接绑定场景 AC：{responsibility.acceptanceIds.join("、") || "未绑定；请对照本片命令和 Task Spec 核对集成责任"}</p>
                      <p>本片场景命令：{responsibility.commandIds.join("、") || "未绑定"}</p>
                      {responsibility.commands.map(command => <div key={command.id}>
                        {command.definitions.length === 1 ? <>
                          <pre className="plan-task-command" tabIndex={0}><code>{text(command.definitions[0].command)}</code></pre>
                          <p>{command.id} · {text(command.definitions[0].owner) || "未声明责任"} · {text(command.definitions[0].tier) || "未声明层级"} · 命令 AC：{strings(command.definitions[0].acceptance_ids).join("、") || "未绑定"}</p>
                        </> : <p>{command.id}：{command.definitions.length ? "定义不唯一，请核对原文" : "命令正文未提供"}</p>}
                      </div>)}
                    </section>)}
                  </details>
                  <SourceRefs refs={scenario.sourceRefs} {...sourceProps} />
                  {scenario.issues.map(issue => <p role="status" key={issue}>{issue}</p>)}
                </article>)}
                <h3>正式验收矩阵</h3>
                {!selected.formalScenarios.length && <p>未绑定独立的正式验收矩阵；已绑定的测试矩阵场景见上方，不代表执行通过。</p>}
                {selected.formalScenarios.map((scenario, i) => <article className="plan-review-card" key={i}><h4>{text(scenario.id)} · {text(scenario.trigger || scenario.title)}</h4><p>{text(scenario.expected)}</p><p>AC：{text(scenario.acceptance_id)} · 命令：{strings(scenario.command_ids).join("、") || "未绑定"}</p><p>{text(scenario.verification_owner)} / {text(scenario.verification_tier)} · {text(scenario.pass_rule)}</p></article>)}
              </>}
              <h3>验收项与验证责任</h3>
              {!selected.criteria.length && <p>未提供结构化验收项，请查看完整原文。</p>}
              {selected.criteria.map((ac, i) => <article className="plan-review-card" key={i}><h4>{ac.id}</h4><p>{ac.statement}</p><p>验证层级：{ac.tier || "未声明"} · 命令：{ac.commandReferences.map(ref => `${ref.id}（${ref.label}）`).join("；") || "未绑定"}</p><SourceRefs refs={ac.sourceRefs} {...sourceProps} /></article>)}
            </section>}
          </>}
        </div>}
      </div>
    </div>
  </div></PlanSourceContext.Provider>;
}
