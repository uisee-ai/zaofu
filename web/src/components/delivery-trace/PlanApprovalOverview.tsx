import type { ReactNode } from "react";
import type { PlanPreview } from "../../api/types";
import { MarkdownText } from "../agent-session/MarkdownText";
import { DecisionList } from "./PlanReviewDetails";
import { buildPlanReview, strings, text } from "./planReviewModel";
import { planReadingSections, type ReadingSection } from "./planReadingSections";

/** Authored business content first; never infer a verdict or trim the original. */
export default function PlanApprovalOverview({ model, excerpts, decisions, decisionSummary,
  preview, projectId, quoted, selectTask, onChanges, onDecisions,
}: {
  model: ReturnType<typeof buildPlanReview>; excerpts: ReturnType<typeof planReadingSections>;
  decisions: Record<string, unknown>[]; decisionSummary: string; preview: PlanPreview; projectId: string;
  quoted: (sections: ReadingSection[]) => ReactNode; selectTask: (id: string) => void;
  onChanges: () => void; onDecisions: () => void;
}) {
  return <>
    <h2>本次交付与审批范围</h2>
    <p>{model.modules.length} 个任务 · {model.criterionCount} 个验收项{model.pageCount !== null ? ` · ${model.pageCount} 个页面` : ""}{model.formCount !== null ? ` · ${model.formCount} 类表单` : ""}</p>
    {model.overview ? <MarkdownText content={model.overview} />
      : excerpts.overview.length ? quoted(excerpts.overview)
      : <p>作者未提供独立业务摘要；以下列出同版任务，详细范围请核对完整原文。</p>}
    <ul>{model.modules.map(module => <li key={module.id}>
      <button type="button" className="delivery-action-button" onClick={() => selectTask(module.id)}>{module.title || module.id}</button>
    </li>)}</ul>
    <h3>本次变更</h3>
    {model.changes ? <MarkdownText content={model.changes} /> : excerpts.changes.length ? quoted(excerpts.changes)
      : <p>本版未提供独立变更说明；不能据此认定没有变更。请比较绑定版本与来源影响。</p>}
    <button type="button" className="delivery-action-button" onClick={onChanges}>查看变更与保留义务</button>
    <h3>风险与待决定事项</h3>
    {model.risks ? <MarkdownText content={model.risks} /> : <p>本版未单列风险摘要；以下决定登记不能代替完整风险评估。</p>}
    <p>{decisionSummary}</p>
    <DecisionList decisions={decisions} preview={preview} projectId={projectId} />
    {excerpts.decisions.length > 0 && <button type="button" className="delivery-action-button" onClick={onDecisions}>核对同版决定原文</button>}
    <h3>交付顺序与完成条件</h3>
    {model.modules.map(module => <article className="plan-review-card" key={module.id}>
      <button type="button" className="delivery-action-button" onClick={() => selectTask(module.id)}>{module.id} · {module.title}</button>
      <p>前置：{module.dependencies.join("、") || "未声明前置"} · 验收项：{module.criteria.map(ac => ac.id).join("、") || "未提供"}</p>
      <details><summary>任务描述与范围</summary><p>{module.description || "未提供"}</p></details>
    </article>)}
    {(model.journeys || excerpts.journeys.length > 0) && <><h3>业务旅程</h3>
      {model.journeys ? <MarkdownText content={model.journeys} /> : quoted(excerpts.journeys)}</>}
    {model.groups.length > 0 && <><h3>业务与交付模块</h3>{model.groups.map((group, i) => <article className="plan-review-card" key={i}>
      <h4>{text(group.title || group.name || group.module_id || group.id)}</h4><p>{text(group.description || group.summary)}</p>
      {strings(group.task_ids).map(id => <button className="delivery-action-button" type="button" key={id} onClick={() => selectTask(id)}>{model.modules.find(m => m.id === id)?.title || id}</button>)}
    </article>)}</>}
    <details className="plan-review-card"><summary>工程来源与完整约束</summary>
      <p>保留同版原文，不将开篇的来源或授权记录推断为业务摘要。</p>
      {quoted(excerpts.opening.filter(section => model.overview || !excerpts.overview.some(overview => overview.key === section.key)))}
      {model.goals.map((goal, i) => <p key={i}><strong>{text(goal.id)}</strong> {text(goal.statement)}</p>)}
    </details>
  </>;
}
