import { useEffect, useState } from "react";

const reportUrl = new URL("../../data/skill-policy-round2.json", import.meta.url).href;

interface PlannerScore {
  handoff_correct: number;
  observed_reads: number;
  required_reads: number;
  median_seconds: number | null;
}
interface Split {
  matched_pairs: number;
  expected_pairs: number;
  input_saving_percent: number | null;
  arms: { full: PlannerScore; index: PlannerScore };
}
interface Result {
  calibration: { passed: number; cases: unknown[] };
  planner: {
    decision: string;
    client: string;
    model: string;
    effort: string;
    splits: Record<"selection" | "test", Split>;
  };
  verify: {
    decision: string;
    skill: string;
    skill_digest: string;
    model: string;
    client: string;
    effort: string;
    splits: {
      test: {
        matched_pairs: number;
        input_token_saving_percent: number | null;
        arms: {
          current: { correct: number };
          candidate: { correct: number; false_releases: number; missed_required_reads: number };
        };
      };
    };
  };
  interaction: {
    methods: string[];
    splits: {
      test: {
        matched_pairs: number;
        expected_pairs: number;
        interaction_pp: number | null;
        activation_confirmed: boolean;
        arms: Record<string, { correct: number; positive_reads: Record<string, number> }>;
      };
    };
  };
  compatibility: {
    tool: string;
    rows: {
      role: string;
      name: string;
      digest: string;
      backend: string;
      client: string;
      verified_version: string | null;
      static_status: string;
      rules: string[];
      required_by: string[];
    }[];
  };
}

let retained: Promise<Result> | undefined;
function useRoundReport() {
  const [data, setData] = useState<Result | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    retained ??= fetch(reportUrl)
      .then((r) => {
        if (!r.ok) throw Error(String(r.status));
        return r.json() as Promise<Result>;
      })
      .catch((reason) => {
        retained = undefined;
        throw reason;
      });
    retained
      .then((value) => {
        if (active) setData(value);
      })
      .catch(() => {
        if (active) setError(true);
      });
    return () => {
      active = false;
    };
  }, []);
  return { data, error };
}

export function FrozenSkillBasis({
  name,
  digest,
  profile,
  provider,
}: {
  name: string;
  digest: string | null;
  profile: string;
  provider: string | null;
}) {
  const { data, error } = useRoundReport();
  if (error) return <span>参考报告读取失败</span>;
  if (!data) return <span>正在比对冻结版本…</span>;
  const role = profile === "task-verify" ? "verify" : profile === "prd-plan" ? "planner" : null;
  const catalog = data.compatibility.rows.find(
    (r) => r.name === name && r.digest === digest && r.role === role && r.backend === provider,
  );
  if (!catalog) return <span>当前版本与角色缺少匹配的参考试验</span>;
  const measured =
    role === "verify" && provider === "codex" && name === data.verify.skill && digest === data.verify.skill_digest;
  return (
    <div>
      {catalog.required_by.length > 0 && <div>角色包依赖 · {catalog.required_by.join("、")}</div>}
      <div>
        静态预测 · {catalog.static_status}（{catalog.client}）
      </div>
      {measured ? (
        <div>
          同版 Skill 的参考决定 · {data.verify.decision === "retain_required" ? "保留必读" : "限定灰度"}
          <br />
          试验范围 · {data.verify.client} / {data.verify.model} / {data.verify.effort}
        </div>
      ) : (
        <div>独立加载策略增益待证</div>
      )}
    </div>
  );
}

export function SkillPolicyRound() {
  const { data, error } = useRoundReport();
  if (error) return <p role="alert">本轮试验报告无法读取。</p>;
  if (!data) return <p role="status">正在读取冻结试验…</p>;
  const p = data.planner;
  const v = data.verify.splits.test;
  const i = data.interaction.splits.test;
  const compatibility = data.compatibility.rows;
  const flags = compatibility.filter((r) => r.static_status !== "loads");
  return (
    <section aria-label="第二轮评估" className="skill-loading-comparison">
      <h4>Planner 输入清单 · {p.decision === "retain_full" ? "保留完整清单" : "可进入限定工作流试验"}</h4>
      <p>
        {p.client} · {p.model}（{p.effort}）
      </p>
      <div style={{ overflowX: "auto" }}>
        <table className="data-table">
          <caption>短索引相对于完整清单</caption>
          <thead>
            <tr>
              {[
                "案例集",
                "完整配对",
                "短索引交接正确",
                "短索引读取义务已核对",
                "总输入变化",
                "耗时中位数（完整 / 短索引）",
              ].map((x) => (
                <th key={x}>{x}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(["selection", "test"] as const).map((key) => {
              const s = p.splits[key],
                a = s.arms.index,
                change = s.input_saving_percent;
              return (
                <tr key={key}>
                  <th scope="row">{key === "test" ? "保留测试集" : "选择集"}</th>
                  <td>
                    {s.matched_pairs}/{s.expected_pairs}
                  </td>
                  <td>
                    {a.handoff_correct}/{s.matched_pairs}
                  </td>
                  <td>
                    {a.observed_reads}/{a.required_reads}
                  </td>
                  <td>{change == null ? "未知" : (change >= 0 ? "节省 " : "增加 ") + Math.abs(change) + "%"}</td>
                  <td>
                    {s.arms.full.median_seconds?.toFixed(1) ?? "未知"} / {a.median_seconds?.toFixed(1) ?? "未知"} s
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <h4>Task Verify · {data.verify.decision === "retain_required" ? "保留默认必读" : "可进入限定灰度"}</h4>
      <p>
        {data.verify.skill} · 新版题目在保留集的完整提交，必读 {v.arms.current.correct}/{v.matched_pairs}，按需{" "}
        {v.arms.candidate.correct}/{v.matched_pairs}。按需组误放行 {v.arms.candidate.false_releases}，应读未见证{" "}
        {v.arms.candidate.missed_required_reads} 次。按需总输入相对必读
        {v.input_token_saving_percent == null
          ? "未知"
          : (v.input_token_saving_percent < 0 ? "增加 " : "节省 ") + Math.abs(v.input_token_saving_percent) + "%"}
        。
      </p>
      <details>
        <summary>
          两项方法的组合试验 · {i.matched_pairs}/{i.expected_pairs} 个完整配对
        </summary>
        <p>
          A 为 {data.interaction.methods[0]}，B 为 {data.interaction.methods[1]}。
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>方法</th>
              <th>完整提交正确</th>
              <th>相关案例读取见证 A / B</th>
            </tr>
          </thead>
          <tbody>
            {(["neither", "a", "b", "both"] as const).map((arm, n) => (
              <tr key={arm}>
                <th scope="row">{["都移除", "仅 A", "仅 B", "A 与 B"][n]}</th>
                <td>
                  {i.arms[arm].correct}/{i.matched_pairs}
                </td>
                <td>{data.interaction.methods.map((m) => i.arms[arm].positive_reads[m]).join(" / ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>
          {i.interaction_pp == null
            ? "组合增益待证：" +
              (i.activation_confirmed ? "有效配对或组间隔离证据不足。" : "相关案例中缺少方法读取见证。")
            : "组合增益 " + i.interaction_pp + " 个百分点"}
        </p>
      </details>
      <details>
        <summary>静态兼容性与评分器校准</summary>
        <p>
          {data.compatibility.tool} · 检查 {compatibility.length} 个角色、Skill 与客户端组合，{flags.length}{" "}
          项需要关注。
        </p>
        <ul>
          {flags.map((r) => (
            <li key={r.role + r.name + r.client}>
              {r.role} · {r.name} · {r.client} · {r.static_status} · {r.rules.join(" / ")} · 规则验证版本{" "}
              {r.verified_version}
            </li>
          ))}
        </ul>
        <p>
          规则标注客户端版本待验证 {compatibility.filter((r) => r.rules.includes("VERSION-UNVERIFIED")).length}{" "}
          项。确定性评分器符合预期标签 {data.calibration.passed}/{data.calibration.cases.length}。
        </p>
      </details>
      <a href={reportUrl} download="skill-policy-round2.json">
        下载本轮逐例证据
      </a>
    </section>
  );
}
