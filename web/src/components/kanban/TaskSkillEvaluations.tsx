import { useEffect, useState } from "react";
import { cachedGetJson } from "../../api/queryClient";

interface Criterion {
  id: string; result: string; reason: string; critical: boolean; evidence_refs: string[];
}
interface Card {
  attempt_id: string; dispatch_id: string; workflow_run_id: string; operation_id: string;
  recommendation: { action: string }; task_outcome: string; revision: number; digest: string; predecessor: string | null;
  change_reason: string; critical_status: string; criteria: Criterion[];
  score: { passed: number; denominator: number; unknown: number; not_applicable: number; percent: number | null; coverage: number | null };
  skills: { name: string; digest: string | null; auto_inject: boolean | null; load_on_demand: boolean | null; read_status: string; invocation: string; read_event_ids: string[]; invocation_event_ids: string[] }[];
}

export function TaskSkillEvaluations({ taskId, projectId }: { taskId: string; projectId?: string }) {
  const prefix = projectId ? `/api/projects/${encodeURIComponent(projectId)}` : "/api";
  const [history, setHistory] = useState(false);
  const [reload, setReload] = useState(0);
  const [cards, setCards] = useState<Card[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setCards(null);
    setError("");
    void cachedGetJson<{ cards: Card[] }>(`${prefix}/tasks/${encodeURIComponent(taskId)}/skill-evaluations?history=${history}`,
      { signal: controller.signal, bypassCache: true })
      .then(data => { if (!controller.signal.aborted) setCards(data.cards); })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, [prefix, taskId, history, reload]);
  return <section className="subsection" data-testid="task-skill-evaluations">
    <div className="inline-heading"><h3>Task Verify 技能评估</h3>
      <button type="button" onClick={() => setReload(value => value + 1)}>刷新评估</button>
    </div>
    <label><input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} /> 显示历史修订</label>
    {error && <p role="alert">{error}</p>}
    {!error && !cards && <p role="status">正在读取评分卡…</p>}
    {cards?.length === 0 && <p className="empty-text">尚无已发布的评分卡。</p>}
    {cards?.map(card => <details key={JSON.stringify([card.workflow_run_id, card.operation_id, card.attempt_id, card.dispatch_id, card.revision])}>
      <summary>{card.attempt_id} · 修订 {card.revision} · {card.task_outcome} · {card.score.percent == null ? "待评分" : `${card.score.percent}%`}
        {" · "}{card.score.passed}/{card.score.denominator} · {card.critical_status === "failed" ? "关键项失败" : card.critical_status === "pending" ? "关键项待证" : "关键项通过"}</summary>
      <p>未知 {card.score.unknown} · 不适用 {card.score.not_applicable} · 可判定覆盖率 {card.score.coverage == null ? "未知" : `${Math.round(100 * card.score.coverage)}%`}</p>
      <p className="mono">{card.workflow_run_id} / {card.operation_id} / {card.dispatch_id}</p>
      <ul>{card.criteria.map(item => <li key={item.id}>{item.id} · {item.result}{item.critical && " · 关键"}
        <div>{item.reason}</div><div className="mono">{item.evidence_refs.join(" · ")}</div></li>)}</ul>
      <details><summary>Skill 调用与读取（{card.skills.length}）</summary><ul>{card.skills.map(skill =>
        <li key={skill.name}>{skill.name} · {skill.auto_inject ? "自动加载" : skill.load_on_demand ? "按需加载" : "加载策略待证"} · 调用 {skill.invocation} · 读取 {skill.read_status}
          <div className="mono">{skill.digest}</div>
          <div className="mono">{[...skill.invocation_event_ids, ...skill.read_event_ids].join(" · ")}</div></li>)}</ul></details>
      <p>{card.recommendation.action}</p>
      <p className="mono">{card.change_reason} · {card.digest}{card.predecessor && ` ← ${card.predecessor}`}</p>
    </details>)}
  </section>;
}
