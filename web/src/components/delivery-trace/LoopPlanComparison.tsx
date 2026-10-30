import type { LoopViewProjection } from "../../api/types";

/** Compare only admitted task identities with identity-bound observations. */
export function LoopPlanComparison({ view }: { view: LoopViewProjection }) {
  const timeline = view.timeline;
  const baseline = timeline?.baseline;
  const byId = new Map((timeline?.tasks ?? []).map(task => [task.id, task]));
  const planned = new Set(baseline?.task_ids ?? []);
  const additional = (timeline?.tasks ?? []).filter(task => task.attempts.length && !planned.has(task.id));
  return <details className="loop-plan-comparison" data-testid="loop-plan-comparison">
    <summary>Plan vs actual <small>{baseline ? "内核记录的准入任务" : "没有准入任务记录"}</small></summary>
    {!baseline ? <p>当前范围没有可归属的内核准入任务记录；无法比较计划与执行。</p> : <>
      <p>来源：{baseline.event_id} · 任务计划摘要 {baseline.task_map_digest.slice(0, 12)} · 事件 {baseline.seq}。清单来自事件记录，未重新校验文件；状态只表示观察到的尝试，不代表任务已验收。</p>
      <div className="loop-plan-rows">{baseline.task_ids.map(id => {
        const task = byId.get(id);
        return <div key={id}><strong>{id}</strong><span>{task?.attempts.length ? `已观察 ${task.attempts.length} 次尝试` : "尚未观察到尝试"}</span></div>;
      })}</div>
      {additional.length > 0 && <p>计划外或关联未确认的任务：{additional.map(task => task.id).join("、")}</p>}
    </>}
    <p>计划耗时、成本基线或足够可靠的预测依据不可用；偏差、ETA 和成本预测未显示。</p>
  </details>;
}
