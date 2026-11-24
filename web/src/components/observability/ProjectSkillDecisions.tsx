import { useEffect, useState } from "react";
import { cachedGetJson } from "../../api/queryClient";
import { FrozenSkillBasis, SkillPolicyRound } from "./SkillPolicyRound";
import { SkillLoadingReference } from "../kanban/SkillLoadingComparison";

interface Row {
  name: string;
  digest: string | null;
  role: string;
  output_profile_id: string;
  provider: string | null;
  harness_version: string | null;
  model: string | null;
  configured_model: string | null;
  mode: string;
  dispatches: number;
  tasks: number;
  materialized: number;
  observed_reads: number;
  reviewed_dispatches: number;
  confirmed_false_releases: number | null;
  evidence: { task_id: string; attempt_id: string; dispatch_id: string };
}

function Basis({ row }: { row: Row }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>核对策略依据</summary>
      {open && (
        <FrozenSkillBasis
          name={row.name}
          digest={row.digest}
          profile={row.output_profile_id}
          provider={row.provider}
        />
      )}
    </details>
  );
}

export default function ProjectSkillDecisions({ projectId }: { projectId?: string }) {
  const [roundOpen, setRoundOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState(false);
  const prefix = projectId ? "/api/projects/" + encodeURIComponent(projectId) : "/api";
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    setRows(null);
    cachedGetJson<{ rows: Row[] }>(prefix + "/skill-decisions", { signal: controller.signal, bypassCache: true })
      .then((result) => {
        if (!controller.signal.aborted) setRows(result.rows);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [prefix]);
  return (
    <section aria-label="项目 Skill 决策表" className="subsection">
      {error && <p role="alert">项目 Skill 派发记录读取失败。</p>}
      {!rows && !error && <p role="status">正在读取项目派发记录…</p>}
      {rows?.length === 0 && <p>该项目尚无已评分的 Skill 派发。</p>}
      {rows && rows.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <caption>项目派发证据 · 每次派发采用最新评分修订</caption>
            <thead>
              <tr>
                {[
                  "Skill / 版本",
                  "角色",
                  "客户端 / 模型",
                  "实际模式",
                  "任务 / 派发",
                  "物化 / 读取见证",
                  "误放行 / 已复核放行",
                  "策略依据",
                ].map((x) => (
                  <th key={x}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={JSON.stringify([
                    r.name,
                    r.digest,
                    r.role,
                    r.provider,
                    r.harness_version,
                    r.model,
                    r.configured_model,
                    r.mode,
                    r.evidence.dispatch_id,
                  ])}
                >
                  <th scope="row" title={r.digest || undefined}>
                    {r.name}
                    <br />
                    {r.digest?.slice(0, 12) || "版本未知"}
                  </th>
                  <td>{r.role}</td>
                  <td>
                    {r.provider ?? "未知"} · {r.harness_version ?? "版本未知"}
                    <br />
                    {r.model || (r.configured_model ? "配置 " + r.configured_model : "模型未知")}
                  </td>
                  <td>{({ required: "必读", on_demand: "按需" } as Record<string, string>)[r.mode] || "未知"}</td>
                  <td>
                    {r.tasks} / {r.dispatches}
                  </td>
                  <td>
                    {r.materialized} / {r.observed_reads}
                  </td>
                  <td>
                    {r.confirmed_false_releases ?? "未知"} / {r.reviewed_dispatches}
                  </td>
                  <td>
                    <Basis row={r} />
                    <a href={prefix + "/tasks/" + encodeURIComponent(r.evidence.task_id) + "/skill-evaluations"}>
                      任务证据 {r.evidence.task_id}
                    </a>
                    <br />
                    {r.evidence.attempt_id}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <details onToggle={(e) => setRoundOpen(e.currentTarget.open)}>
        <summary>本轮策略决定与依据</summary>
        {roundOpen && <SkillPolicyRound />}
      </details>
      <details onToggle={(e) => setHistoryOpen(e.currentTarget.open)}>
        <summary>首轮 Task Verify 参考试验</summary>
        {historyOpen && <SkillLoadingReference />}
      </details>
    </section>
  );
}
