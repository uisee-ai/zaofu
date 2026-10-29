import { asRecordArray } from "../../app/shared";
import { statusLabels, type SdlcNode, type WorkflowGraphResult } from "./sdlcGraphModel";

export default function WorkflowStageDetails({ node, graph, partial, onSelect, onLoadFull }: {
  node: SdlcNode; graph: WorkflowGraphResult; partial: boolean; onSelect: (id: string) => void; onLoadFull: () => void;
}) {
  const stage = String(node.data.stage_id || node.id.replace("workflow:stage:", ""));
  const roles = asRecordArray(node.data.roles).map(role => String(role.instance_id || role.name || "")).filter(Boolean);
  const priority = (status: string) => ["failed", "blocked", "rejected", "evidence_missing"].includes(status) ? 0 : ["running", "in_progress"].includes(status) ? 1 : 2;
  const operations = partial ? [] : graph.nodes.filter(item => item.kind === "operation" && String(item.data.parent_stage_id || "") === stage)
    .sort((a, b) => priority(a.status) - priority(b.status) || a.label.localeCompare(b.label) || a.id.localeCompare(b.id));
  const tasks = [...new Set(operations.map(item => String(item.data.task_id || "")).filter(Boolean))];
  return <section className="sdlc-stage-details" aria-label="阶段执行摘要">
    <h4>阶段摘要</h4>
    <p>{roles.length ? `已配置角色：${roles.join("、")}` : "角色配置未记录"}</p>
    {partial ? <><p>执行与 Task 详情尚未加载。</p><button type="button" onClick={onLoadFull}>加载阶段运行详情</button></> : <>
      <p>已观测：{operations.length} 个操作 · {tasks.length} 个关联 Task</p>
      {tasks.length > 0 && <p>Task：{tasks.join("、")}</p>}
      {operations.slice(0, 3).map(operation => <button key={operation.id} type="button" onClick={() => onSelect(operation.id)}>
        {operation.label} · {statusLabels[operation.status] || operation.status}
      </button>)}
      {operations.length > 3 && <details><summary>查看其他 {operations.length - 3} 个操作</summary>{operations.slice(3).map(operation => <button key={operation.id} type="button" onClick={() => onSelect(operation.id)}>{operation.label} · {statusLabels[operation.status] || operation.status}</button>)}</details>}
      {!operations.length && <p>尚未观测到阶段操作。</p>}
    </>}
  </section>;
}
