import { useEffect, useState } from "react";
import "./skillLoadingComparison.css";

const reportUrl = new URL("../../data/skill-loading-reference.json", import.meta.url).href;

export interface LoadingContext {
  provider?: string | null;
  model?: string | null;
  configured_model?: string | null;
  skills: { name: string; digest: string | null; auto_inject: boolean | null; load_on_demand: boolean | null }[];
}

type Arm = "raw" | "current" | "candidate";
interface ArmScore {
  complete: number; verdict_correct: number; correct: number; false_releases: number; false_blocks: number; observed_reads: number;
  missed_required_reads: number; unrelated_reads: number; mean_input_tokens: number | null; median_seconds: number | null;
}
interface Split {
  expected_pairs: number; matched_pairs: number; quality_regressions: number; input_token_saving_percent: number | null;
  arms: Record<Arm, ArmScore>;
}
interface Report {
  skill: string; model: string; client: string; effort: string; skill_digest: string; decision: string;
  policy: { minimum_input_token_saving_percent: number };
  observations: { case: string; repeat: number; arm: Arm; split: string; correct: boolean; complete: boolean;
    reference: string[]; answer: Record<string, unknown> | null }[];
  reasons: string[]; splits: Record<"selection" | "test", Split>;
}
const arms: [Arm, string][] = [["raw","移除"], ["current","默认必读"], ["candidate","按需加载"]];
const reasonNames: Record<string, string> = {
  coverage:"样本覆盖不足", missing_pairs:"三组配对不完整", quality_regression:"原本答对的案例出现退步",
  false_releases:"出现关键误放行", missed_required_reads:"需用方法时缺少读取见证", unrelated_reads:"无关任务触发读取",
  required_loading_unconfirmed:"默认必读组缺少读取见证", raw_contamination:"移除组仍读到了目标方法", cost_goal:"输入 token 节省未达预设目标",
};

export default function SkillLoadingComparison({ cards }: { cards: LoadingContext[] }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState(false);
  const [split, setSplit] = useState<"selection" | "test">("test");
  useEffect(() => {
    const controller = new AbortController();
    fetch(reportUrl, { signal: controller.signal }).then(response => {
      if (!response.ok) throw Error(String(response.status));
      return response.json();
    }).then(data => { if (!controller.signal.aborted) setReport(data); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, []);
  if (error) return <p role="alert">加载策略报告读取失败。</p>;
  if (!report) return <p role="status">正在读取对照报告…</p>;
  const metrics = report.splits[split];
  const matches = cards.flatMap(card => card.skills.filter(s => s.name === report.skill).map(s => {
    const mode = s.auto_inject ? "默认必读" : s.load_on_demand ? "按需加载" : "策略未知";
    return [card.provider || "客户端未知", card.model || card.configured_model || "模型未知", mode,
      !s.digest ? "版本未知" : s.digest === report.skill_digest ? "Skill 摘要一致" : "Skill 摘要不同"].join(" · ");
  }));
  const misses = report.observations.filter(r => r.split === split && r.arm === "candidate" && r.complete && !r.correct);
  const savings = metrics.input_token_saving_percent;
  const costChange = savings == null ? "未知" : (savings < 0 ? "增加 " : "节省 ") + Math.abs(savings) + "%";
  return <section className="skill-loading-comparison" aria-label="Skill 加载策略参考试验">
    <h4>{report.decision === "canary_candidate" ? "建议进入限定灰度" : "保留默认必读"}</h4>
    <p>{report.skill} · {report.client} · {report.model}（{report.effort}）</p>
    <p>本任务派发记录 · {Array.from(new Set(matches)).join("；") || "尚无此 Skill 的记录"}</p>
    <label>案例集 <select value={split} onChange={e => setSplit(e.target.value as typeof split)}>
      <option value="test">保留测试集</option><option value="selection">选择集</option>
    </select></label>
    <p>完整三组配对 {metrics.matched_pairs}/{metrics.expected_pairs} · 按需组平均输入 token {costChange}（目标节省 ≥ {report.policy.minimum_input_token_saving_percent}%）</p>
    <div style={{ overflowX: "auto" }}><table className="data-table">
      <caption>同案同次配对结果</caption>
      <thead><tr>{["加载策略","判词正确","完整提交正确","误放行","误阻塞","观察到读取","平均输入 token","耗时中位数"].map(s => <th key={s}>{s}</th>)}</tr></thead>
      <tbody>{arms.map(([key, label]) => {
        const row = metrics.arms[key];
        return <tr key={key}><th scope="row">{label}</th><td>{row.verdict_correct}/{metrics.matched_pairs}</td><td>{row.correct}/{metrics.matched_pairs}</td>
          <td>{row.false_releases}</td><td>{row.false_blocks}</td><td>{row.observed_reads}</td>
          <td>{row.mean_input_tokens?.toLocaleString() ?? "未知"}</td><td>{row.median_seconds == null ? "未知" : row.median_seconds+" s"}</td></tr>;
      })}</tbody></table></div>
    <p>按需组应读未读 {metrics.arms.candidate.missed_required_reads} 次，无关读取 {metrics.arms.candidate.unrelated_reads} 次。</p>
    {misses.length > 0 && <details>
      <summary>查看按需组的 {misses.length} 次提交偏差</summary>
      <ul>{misses.map(row => <li key={row.case + row.repeat}>
        <strong>{row.case} · 第 {row.repeat} 次</strong>
        <div>参考判词 / 动作 / 收据 · {row.reference.join(" / ")}</div>
        <code>{JSON.stringify(row.answer)}</code>
      </li>)}</ul>
    </details>}
    {report.reasons.length > 0 && <p>保留原因 · {Array.from(new Set(report.reasons.map(s => (s.startsWith("test:") ? "保留测试集 · " : s.startsWith("selection:") ? "选择集 · " : "") + (reasonNames[s.split(":").pop()!] || s)))).join("、")}</p>}
    <a href={reportUrl} download="task-verify-skill-loading.json">下载逐例 JSON</a>
  </section>;
}
