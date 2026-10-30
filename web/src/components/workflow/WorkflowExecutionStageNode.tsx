import { useState } from 'react';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { statusLabels, workflowNodeLabel, type SdlcNode } from './sdlcGraphModel';
import { observedWorkers, stageWorkers, workerActivity } from './executionGraphModel';

type StageNode = Node<{ node: SdlcNode; allNodes: SdlcNode[]; partial: boolean; operationIds?: string[]; mode: 'aggregate' | 'records'; onSelect: (id: string) => void }, 'executionStage'>;

export function WorkflowExecutionStageNode({ data }: NodeProps<StageNode>) {
  const [expanded, setExpanded] = useState(false);
  const { node, allNodes, partial, onSelect, mode } = data;
  const stageId = String(node.data.stage_id || node.id.replace('workflow:stage:', ''));
  const roleNode = node.id.startsWith('workflow:role:');
  const unassigned = Boolean(node.data.unassigned_execution);
  const workers = roleNode || unassigned ? observedWorkers(allNodes.filter(item => data.operationIds?.includes(item.id))) : stageWorkers(allNodes, stageId);
  const roles = new Set(workers.map(worker => worker.role)).size;
  const records = workers.reduce((sum, worker) => sum + worker.records.length, 0);
  const items = mode === 'records' ? workers.flatMap(worker => worker.records.map(record => ({ ...worker, latest: record, records: [record] }))) : workers;
  const visible = expanded ? items : items.slice(0, 2);
  return <div className="execution-graph-stage">
    <Handle type="target" position={Position.Left} />
    <header><div><small>{roleNode ? '角色执行' : unassigned ? '执行归属' : '阶段'}</small><strong title={node.label}>{workflowNodeLabel(node)}</strong></div><span>{roleNode || unassigned ? '已观测' : statusLabels[node.status] || node.status}</span></header>
    <p className="execution-graph-count">{partial ? '执行记录未加载' : `${roles} 个已观测角色 · ${records} 条执行记录`}</p>
    <div className={`execution-graph-worker-list${expanded ? ' is-expanded nowheel' : ''}`}>{visible.map(worker => {
      const activity = workerActivity(worker.latest);
      const time = activity?.time ? new Date(activity.time) : null;
      const displayTime = time && !Number.isNaN(time.valueOf()) ? time.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : activity?.time;
      const task = worker.taskId ? allNodes.find(item => item.kind === 'task' && String(item.data.task_id || '') === worker.taskId) : undefined;
      return <button className="execution-graph-worker nodrag" type="button" key={mode === 'records' ? worker.latest.id : JSON.stringify([worker.role, worker.taskId])} onClick={event => { event.stopPropagation(); onSelect(worker.latest.id); }}>
        <span className="execution-worker-heading"><b>{worker.role}</b><em>最近观测：{statusLabels[worker.latest.status] || worker.latest.status}</em></span>
        <span title={task?.label || worker.taskId}>Task：{task ? `${worker.taskId} · ${task.label}` : worker.taskId || '未关联'}</span>
        <span title={activity?.time || ''}>最近活动：{activity ? `${activity.label}${activity.time ? ` · ${displayTime}` : ''}` : '活动未观测'}</span>
        {mode === 'aggregate' && worker.records.length > 1 && <span>{worker.records.length} 条执行记录</span>}
        {mode === 'records' && <span title={worker.latest.id}>记录：{String(worker.latest.data.active_attempt_id || worker.latest.id)}</span>}
      </button>;
    })}</div>
    {!partial && !workers.length && <p className="execution-graph-empty">尚未观测到 Worker 执行</p>}
    {items.length > 2 && <button className="execution-graph-more nodrag" type="button" aria-expanded={expanded} onClick={event => { event.stopPropagation(); setExpanded(!expanded); }}>{expanded ? '收起' : `展开全部 ${items.length} ${mode === 'records' ? '条执行记录' : '组角色 / Task'}`}</button>}
    <Handle type="source" position={Position.Right} />
  </div>;
}
