import { useEffect, useState } from "react";
import type { PlanPreview } from "../../api/types";
import { SourceRefs } from "./PlanReviewDetails";
import { dispositionLabel } from "./planTraceModel";

type Profile = { unavailable_ports: string[]; profile: string; scope: string; reason: string; ports: string[] | null; paths: string[] | null };
type Task = { task_id: string; title: string; acceptance_ids: string[]; source_refs: string[]; image_refs: string[];
  dependencies: string[]; profiles: Profile[]; issues: string[]; complete: boolean; navigation_complete: boolean };
type Report = { plan_id: string; binding_digest: string; items: Task[]; total: number; next_offset: number | null;
  extra_partition_ids: string[]; extra_partition_count: number;
  source_index_available: boolean; source_inventory_available: boolean; source_total: number; source_status_complete: boolean;
  source_status: { source_id: string; disposition: string; reason: string; source_ref: string }[] };
const labels: Record<string, string> = { implementation: "实施", "task-verify": "独立任务验证", "integration-acceptance-review": "任务集成评审", "candidate-verify": "候选整体验证", "thin-judge-goal-closure": "目标闭环评审" };
const issueLabel = (value: string) => {
  const [code, ...detail] = value.split(":");
  const label: Record<string,string> = { reference_lookup_incomplete: "导航未完整，引用待核实", source_reference_missing: "切片缺少来源引用", source_reference_unknown: "来源引用未在索引声明", acceptance_reference_missing: "切片未声明验收引用", image_reference_missing: "切片未声明参考图引用", source_mapping_unknown: "来源映射不可用", source_not_mapped: "未映射来源", task_execution_input_unavailable: "任务输入未绑定", legacy_full_input: "旧格式按完整输入读取", partition_invalid: "任务切片结构无效", invalid_selection_declaration: "角色选择声明无效", unknown_dependency: "依赖任务无效", task_id_missing_or_duplicated: "任务 ID 缺失或重复" };
  return `${label[code] ?? code}${detail.length ? `：${detail.join(":")}` : ""}`;
};

export default function PlanInputReviewPanel({ preview, projectId }: { preview: PlanPreview; projectId: string }) {
  const [offset, setOffset] = useState(0);
  const [draft, setDraft] = useState("");
  const [task, setTask] = useState("");
  const [result, setResult] = useState<{ key: string; data?: Report; error?: string } | null>(null);
  const key = `${projectId}:${preview.plan_id}:${JSON.stringify(preview.artifacts)}:${offset}:${task}`;
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ offset: String(offset), task, limit: "5" });
    void fetch(`/api/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(preview.plan_id)}/input-review?${params}`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return await response.json() as Report; })
      .then(data => { if (!controller.signal.aborted) setResult({ key, data }); })
      .catch(error => { if (!controller.signal.aborted) setResult({ key, error: String(error) }); });
    return () => controller.abort();
  }, [key, offset, task, projectId, preview.plan_id]);
  const current = result?.key === key ? result : null;
  return <section aria-label="任务输入审查">
    <h2>任务输入审查</h2>
    <p>按本版声明审查各角色将读取的材料。引用齐全不代表语义完整或验收通过；实际派发仍以批准的输入绑定为准。</p>
    <form onSubmit={event => { event.preventDefault(); setTask(draft); setOffset(0); }}>
      <label>精确任务 ID<input value={draft} maxLength={256} onChange={event => setDraft(event.target.value)} /></label><button type="submit">查看任务输入</button>
    </form>
    {!current && <p role="status">正在读取本版输入声明…</p>}
    {current?.error && <p role="alert">输入审查不可用：{current.error}。请查看同版原始工件。</p>}
    {current?.data && <>
      <p>{current.data.total} 个匹配任务 · 来源 {current.data.source_total} 条</p>
      {current.data.extra_partition_count > 0 && <p role="alert">存在未归属任务的切片：{current.data.extra_partition_ids.join("、")}</p>}
      {!current.data.source_index_available && <p role="alert">来源索引未绑定，覆盖范围未知。</p>}
      {!current.data.source_inventory_available && <p>页面和原始参考图范围未知。</p>}
      <details><summary>来源去向</summary>
        {current.data.source_status.map((row, i) => <p key={i}>{row.source_id} · {dispositionLabel(row.disposition)} · {row.reason || "未声明理由"}</p>)}
        {!current.data.source_status_complete && <p>这里只展示部分来源，请在覆盖审查查看完整清单。</p>}
      </details>
      {current.data.items.map((item, i) => <article className="plan-review-card" key={`${item.task_id}:${i}`}>
        <h3>{item.task_id} · {item.title}</h3>
        <p>验收项：{item.acceptance_ids.join("、") || "未声明"} · 依赖：{item.dependencies.join("、") || "未声明"}</p>
        <p>原始参考图：{item.image_refs.join("、") || "未声明映射"}（图像状态与视觉要求需独立核对）</p>
        <SourceRefs refs={item.source_refs} preview={preview} projectId={projectId} />
        {item.profiles.map(profile => <details key={profile.profile}><summary>{labels[profile.profile] ?? profile.profile}</summary>
          <p>{profile.reason}</p><p>输入：{profile.ports?.join("、") || "保持该角色既有必读政策"}</p>
          {profile.unavailable_ports.length > 0 && <p>未确认绑定的输入：{profile.unavailable_ports.join("、")}</p>}
          {profile.paths && <p>任务范围：{profile.paths.join("、")}</p>}
        </details>)}
        {item.issues.map(issue => <p role="status" key={issue}>{issueLabel(issue)}</p>)}
        {(!item.complete || !item.navigation_complete) && <p>本视图或导航未完整展开，需核对原始输入。</p>}
      </article>)}
      <div className="button-row">
        {offset > 0 && <button type="button" onClick={() => setOffset(0)}>返回首组任务</button>}
        {current.data.next_offset !== null && <button type="button" onClick={() => setOffset(current.data!.next_offset!)}>下一组任务输入</button>}
      </div>
    </>}
  </section>;
}
