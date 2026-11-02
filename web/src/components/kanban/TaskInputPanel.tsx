import { useEffect, useState } from "react";
import { TaskInputCosts } from "./TaskInputCosts";
import { cachedGetJson } from "../../api/queryClient";

interface InputEntry {
  source_id: string; artifact_id: string; occurrence_id: string; json_path: string;
  sha256: string; ref: string; required: boolean | null; read_status: string;
  source_bytes: number | null; selected_bytes: number | null; snapshot_status: string;
  read_command: string;
  locator_id?: string; navigation_count?: number; navigation_complete?: boolean;
  navigation?: { kind: string; id?: string; relation?: string; title?: string; line?: number; field_pointer?: string };
  cost: { produced_body_bytes: number; repeated_exact_range_bytes: number; read_count: number };
}
interface InputIndex {
  attempt_id: string; task_id: string; read_purpose: string; total: number;
  items: InputEntry[]; next_cursor: string | null;
  lifecycle?: { status: string; requires_rebind?: boolean };
  recovery_command?: string;
  navigation_complete?: boolean | null;
}
const bytes = (value: number | null) => value == null ? "unknown" : `${value.toLocaleString()} B`;

export function TaskInputPanel({ attemptId, projectId }: { attemptId: string; projectId?: string }) {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [navigate, setNavigate] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<{ key: string; value?: InputIndex; error?: string } | null>(null);
  const prefix = projectId ? `/api/projects/${encodeURIComponent(projectId)}` : "/api";
  const params = new URLSearchParams({ query, limit: "10" });
  if (navigate) params.set("navigate", "true");
  if (cursor) params.set("cursor", cursor);
  const path = `${prefix}/attempts/${encodeURIComponent(attemptId)}/inputs?${params}`;
  const key = `${path}:${reload}`;
  useEffect(() => {
    if (!attemptId) return;
    const controller = new AbortController();
    void cachedGetJson<InputIndex>(path, { signal: controller.signal, bypassCache: true })
      .then(value => { if (!controller.signal.aborted) setResult({ key, value }); })
      .catch(error => { if (!controller.signal.aborted) setResult({ key, error: String(error) }); });
    return () => controller.abort();
  }, [attemptId, key, path]);
  const current = result?.key === key ? result : null;
  const data = current?.value;
  return <section className="subsection" data-testid="task-input-panel">
    <div className="inline-heading"><h3>Task Inputs</h3>
      {attemptId && <button type="button" onClick={() => { setCursor(null); setReload(value => value + 1); }}>刷新输入</button>}
    </div>
    {!attemptId ? <p className="empty-text">当前任务没有绑定的派发输入。</p> : <>
      <TaskInputCosts key={`${projectId}:${attemptId}`} attemptId={attemptId} projectId={projectId} />
      <p className="muted">仅显示此派发授权的输入目录；目录、历史回执均不证明当前已理解正文。Provider token/cache 未观测。</p>
      <form onSubmit={event => { event.preventDefault(); setQuery(draft); setCursor(null); }}>
        <label>来源 / 路径 / 需求 ID / 章节 <input aria-label="输入定位" value={draft} maxLength={256} onChange={event => setDraft(event.target.value)} /></label>
        <label><input type="checkbox" checked={navigate} onChange={event => { setNavigate(event.target.checked); setCursor(null); }} />展开关系与章节</label>
        <button type="submit">定位</button>
      </form>
      {!current && <p role="status">加载输入目录…</p>}
      {current?.error && <p role="alert">输入目录不可用：{current.error}</p>}
      {data && <>
        <p className="muted">{data.task_id} · {data.read_purpose || "未声明角色用途"} · {data.total} 个可见定位</p>
        <p>输入版本：{({ current: "当前已准入", pinned: "固定输入（未绑定 Plan Package）", superseded: "已被新版本替代", rejected: "准入已拒绝或取消", unadmitted: "尚未准入", unknown: "无法确认" } as Record<string, string>)[data.lifecycle?.status ?? "unknown"] ?? "无法确认"}</p>
        {data.lifecycle?.requires_rebind && <p role="alert">此派发的输入版本需要受控重新绑定；当前目录仍展示原版本，不能用它直接恢复执行。</p>}
        {data.recovery_command && <details data-testid="input-recovery-entry"><summary>恢复输入</summary>
          <p>在绑定此派发的 worker 会话中查看恢复清单。检查点和历史已读均不代表当前记忆，也不授予执行权限。</p>
          <pre className="text-block">{data.recovery_command}</pre>
        </details>}
        {data.navigation_complete === false && <p role="alert">授权范围的导航未完整建立；未命中仍需核对固定原文。</p>}
        {!data.items.length && <p className="empty-text">没有匹配的授权输入；不代表项目没有相关需求。</p>}
        {data.items.map(item => <details key={item.locator_id ?? `${item.occurrence_id}:${item.json_path}`}>
          <summary><span className="mono">{item.source_id} · {item.json_path}{item.navigation && ` · ${item.navigation.id ?? item.navigation.title}`}</span> — {item.required == null ? "必读政策未知" : item.required ? "必读" : "按需"} · {bytes(item.selected_bytes)}</summary>
          {item.navigation && <p>{item.navigation.kind === "section" ? `章节 · 第 ${item.navigation.line} 行` : `声明关系 ${item.navigation.relation} · ${item.navigation.field_pointer}`}；定位不代表验收覆盖。</p>}
          {item.navigation_complete === false && <p>此输入的导航达到提取上限；未列出的关系不代表不存在。</p>}
          <p>来源 {item.artifact_id} · {item.snapshot_status === "pinned" ? "固定选择快照" : "旧版来源（尺寸未知）"}</p>
          <p>原文 {bytes(item.source_bytes)} / 选择 {bytes(item.selected_bytes)} / 已输出正文 {bytes(item.cost.produced_body_bytes)} / 相同范围重读 {bytes(item.cost.repeated_exact_range_bytes)}</p>
          <p>读取记录：{item.read_status === "recorded" ? "已有历史完整回执" : item.read_status === "missing" ? "必读回执未完整" : item.read_status === "unknown" ? "必读政策不可用" : "可选材料"}；跨范围重叠不计入重读估计。</p>
          <p className="mono">{item.ref}<br />sha256: {item.sha256}</p>
          <p>在绑定此派发的 worker 会话回读：</p><pre className="text-block">{item.read_command}</pre>
        </details>)}
        <div className="button-row">
          {cursor && <button type="button" onClick={() => setCursor(null)}>返回首页</button>}
          {data.next_cursor && <button type="button" onClick={() => setCursor(data.next_cursor)}>下一页</button>}
        </div>
      </>}
    </>}
  </section>;
}
