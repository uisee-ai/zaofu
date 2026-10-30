import { relatedNodes, statusLabels, type SdlcEdge, type SdlcNode, type WorkflowGraphResult } from './sdlcGraphModel.js';

export function observedRunStatus(run: SdlcNode): string {
  if (['done', 'completed', 'failed', 'blocked', 'cancelled', 'abandoned'].includes(run.status) && !run.data.terminal_event_id) return '终态证据未观测';
  return statusLabels[run.status] || run.status;
}

/** Context cards and associations are read-only presentation, not runtime events. */
export function executionContext(graph: WorkflowGraphResult, visibleNodes: SdlcNode[], visibleEdges: SdlcEdge[], objective: string) {
  const identity = 'goal_coverage' in graph ? graph.goal_coverage?.identity : undefined;
  const boundGoal = identity?.goal_id && identity.workflow_run_id === graph.scope.run_id
    ? graph.nodes.find(node => node.kind === 'goal' && node.id === `goal:${identity.goal_id}`) : undefined;
  const sourceGoal = boundGoal || graph.nodes.find(node => node.kind === 'requirement');
  const sourceRun = graph.nodes.find(node => node.kind === 'run' || node.kind === 'preparation');
  const goal: SdlcNode = sourceGoal ? { ...sourceGoal, data: { ...sourceGoal.data, context_kind: 'goal' } } : {
    id: `request:${graph.scope.request_id}`, kind: 'requirement', label: objective || '目标未加载', status: 'unknown', views: ['workflow'], data: { context_kind: 'goal', context_placeholder: true },
  };
  const run: SdlcNode = sourceRun ? { ...sourceRun, data: { ...sourceRun.data, context_kind: 'run' } } : {
    id: `run:${graph.scope.run_id || graph.scope.request_id}`, kind: graph.scope.execution_started ? 'run' : 'preparation',
    label: graph.scope.run_id || '尚未启动', status: 'unknown', views: ['workflow'], data: { context_kind: 'run', context_placeholder: true },
  };
  const terminal = String(sourceRun?.data.terminal_event_id || '');
  const verdicts: Record<string, string> = { done: '已完成', completed: '已完成', failed: '失败', blocked: '阻塞', cancelled: '已取消', abandoned: '已放弃' };
  const resultLabel = terminal && verdicts[run.status] ? verdicts[run.status] : !sourceRun && graph.scope.execution_started ? '运行结果未加载' :
    graph.scope.execution_started === false || run.kind === 'preparation' ? '尚未启动' : verdicts[run.status] ? '结束证据未观测' : '尚未观测到结束';
  const result: SdlcNode = { id: `ui:run-result:${graph.scope.request_id}`, kind: 'run_result', label: resultLabel,
    status: terminal && verdicts[run.status] ? run.status : 'unknown', views: ['workflow'],
    data: { context_kind: 'result', context_target: run.id, terminal_event_id: terminal, context_placeholder: true } };
  const links: SdlcEdge[] = [];
  const realGoalLink = graph.edges.find(edge => edge.source === goal.id && edge.target === run.id && edge.relation === 'proposes');
  links.push(realGoalLink ? { ...realGoalLink, views: ['workflow'], presentation: 'goal' } : {
    id: `ui:goal-scope:${goal.id}:${run.id}`, source: goal.id, target: run.id,
    relation: boundGoal ? 'Goal 上下文' : '需求上下文', views: ['workflow'], presentation: 'scope',
  });
  const visible = new Set(visibleNodes.map(node => node.id));
  const observed = new Set<string>();
  const runId = graph.scope.run_id;
  if (runId && graph.scope.execution_started !== false) {
    for (const operation of graph.nodes) {
      if (operation.kind !== 'operation' || !operation.data.role_instance || operation.data.workflow_run_id !== runId) continue;
      const stage = `workflow:stage:${String(operation.data.parent_stage_id || '')}`;
      if (visible.has(stage)) observed.add(stage);
      else {
        let mapped = false;
        for (const edge of graph.edges) if (edge.relation === 'executes' && edge.target === operation.id && edge.source.startsWith('workflow:role:') && visible.has(edge.source)) { observed.add(edge.source); mapped = true; }
        const unassigned = visibleNodes.find(node => node.data.unassigned_execution && Array.isArray(node.data.operation_ids) && node.data.operation_ids.includes(operation.id));
        if (!mapped && unassigned && graph.edges.some(edge => edge.source === run.id && edge.target === operation.id && edge.relation === 'executes')) observed.add(unassigned.id);
      }
    }
  }
  // Connect only the first observed member of each declared path. These lines
  // express Run membership; they do not claim which actor dispatched whom.
  for (const target of observed) {
    const upstream = relatedNodes(target, visibleEdges, 'upstream');
    if ([...observed].some(other => {
      if (other === target || !upstream.has(other)) return false;
      const sameCycle = relatedNodes(other, visibleEdges, 'upstream').has(target);
      return !sameCycle || other.localeCompare(target) < 0;
    })) continue;
    links.push({ id: `ui:run-scope:${run.id}:${target}`, source: run.id, target, relation: '运行归属', views: ['workflow'], presentation: 'scope' });
  }
  // A terminal gate connects to the outcome only when both share the actual
  // Kernel terminal event. A planned gate alone is not a Run verdict.
  const gate = visibleNodes.find(node => node.id.startsWith('workflow:terminal:') && terminal && node.data.terminal_event_id === terminal);
  if (gate) links.push({ id: `ui:result:${gate.id}:${result.id}`, source: gate.id, target: result.id,
    relation: '共同终态证据', views: ['workflow'], presentation: 'outcome' });
  return { goal, run, result, links };
}

/** Operations whose graph explicitly places them under the Run stay visible. */
export function unassignedExecutionGroup(graph: WorkflowGraphResult): SdlcNode | null {
  const run = graph.nodes.find(node => node.kind === 'run');
  if (!run || !graph.scope.run_id) return null;
  const directlyOwned = new Set(graph.edges.filter(edge => edge.source === run.id && edge.relation === 'executes').map(edge => edge.target));
  const operationIds = graph.nodes.filter(node => node.kind === 'operation' && node.data.role_instance &&
    node.data.workflow_run_id === graph.scope.run_id && directlyOwned.has(node.id)).map(node => node.id);
  if (!operationIds.length) return null;
  return { id: `ui:unassigned-workers:${graph.scope.request_id}`, kind: 'workflow', label: '未归属阶段', status: 'unknown',
    views: ['workflow'], data: { unassigned_execution: true, operation_ids: operationIds } };
}
