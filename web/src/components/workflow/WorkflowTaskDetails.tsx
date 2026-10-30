import { useEffect, useState } from 'react';
import { statusLabels, type SdlcEdge, type SdlcNode } from './sdlcGraphModel';

export default function WorkflowTaskDetails({ task, nodes, edges, operations, onTask, onOperation, onFocus }: {
  task: SdlcNode; nodes: SdlcNode[]; edges: SdlcEdge[]; operations: SdlcNode[];
  onTask: (id: string) => void; onOperation: (id: string) => void; onFocus: () => void;
}) {
  const [limit, setLimit] = useState(12);
  useEffect(() => setLimit(12), [task.id]);
  const lookup = new Map(nodes.map(node => [node.id, node]));
  const upstream = edges.filter(edge => edge.relation === 'depends_on' && edge.target === task.id);
  const downstream = edges.filter(edge => edge.relation === 'depends_on' && edge.source === task.id);
  const relation = (id: string) => { const node = lookup.get(id); return node ? `${node.label} · ${statusLabels[node.status] || node.status}` : `${id} · 节点未加载`; };
  return <section aria-label="Task 依赖和执行">
    <h4>任务依赖</h4>
    <p>上游依赖：{upstream.length ? '' : '未声明'}</p>{upstream.map(edge => <button type="button" key={edge.id} onClick={() => onTask(edge.source)}>← {relation(edge.source)}</button>)}
    <p>下游影响：{downstream.length ? '' : '未声明'}</p>{downstream.map(edge => <button type="button" key={edge.id} onClick={() => onTask(edge.target)}>→ {relation(edge.target)}</button>)}
    <button type="button" onClick={onFocus}>聚焦任务上下游</button>
    <h4>关联执行 · {operations.length} 条</h4>
    <p className="sdlc-activity-hint">选择具体记录查看；多次执行不代表多个活跃 Worker。</p>
    {operations.length ? operations.slice(0, limit).map(op => <button key={op.id} type="button" onClick={() => onOperation(op.id)}>
      <span>{String(op.data.parent_stage_id || '阶段未记录')} · {String(op.data.role_instance || '角色未记录')} · {statusLabels[op.status] || op.status}<small>{String(op.data.last_event_at || '时间未观测')} · {String(op.data.active_attempt_id || op.id)}</small></span>
    </button>) : <p>尚无已关联的执行记录。</p>}
    {limit < operations.length && <button type="button" onClick={() => setLimit(value => value + 12)}>加载更多关联执行</button>}
  </section>;
}
