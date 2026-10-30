import { asRecordArray } from '../../app/shared';
import { statusLabels, type SdlcNode, type WorkflowGraphResult } from './sdlcGraphModel';
import { observedWorkers, stageWorkers, unmappedRoleOperationIds, workerActivity } from './executionGraphModel';

export default function WorkflowStageDetails({ node, graph, partial, onSelect, onLoadFull }: {
  node: SdlcNode; graph: WorkflowGraphResult; partial: boolean; onSelect: (id: string) => void; onLoadFull: () => void;
}) {
  const stage = String(node.data.stage_id || node.id.replace('workflow:stage:', ''));
  const roles = asRecordArray(node.data.roles).map(role => String(role.instance_id || role.name || '')).filter(Boolean);
  const roleNode = node.id.startsWith("workflow:role:");
  const operationIds = new Set(unmappedRoleOperationIds(node.id, graph.nodes, graph.edges));
  const workers = partial ? [] : roleNode ? observedWorkers(graph.nodes.filter(item => operationIds.has(item.id))) : stageWorkers(graph.nodes, stage);
  return <section className="sdlc-stage-details" aria-label="阶段执行摘要">
    <h4>阶段摘要</h4>
    <p>{roles.length ? `已配置角色：${roles.join('、')}` : '角色配置未记录'}</p>
    {partial ? <><p>执行与 Task 详情尚未加载。</p><button type="button" onClick={onLoadFull}>加载阶段运行详情</button></> : <>
      <p>已观测：{new Set(workers.map(worker => worker.role)).size} 个角色 · {workers.reduce((sum, worker) => sum + worker.records.length, 0)} 条执行记录</p>
      {workers.map(worker => <details key={JSON.stringify([worker.role, worker.taskId])} open>
        <summary>{worker.role} · {worker.taskId || 'Task 未关联'}</summary>
        {worker.records.map(operation => { const activity = workerActivity(operation); return <button key={operation.id} type="button" onClick={() => onSelect(operation.id)}>
          {statusLabels[operation.status] || operation.status} · {String(operation.data.last_event_at || '时间未观测')} · {activity?.label || '活动未观测'}
        </button>; })}
      </details>)}
      {!workers.length && <p>尚未观测到 Worker 执行。</p>}
    </>}
  </section>;
}
