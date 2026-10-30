import type { SdlcNode, SdlcEdge } from './sdlcGraphModel';

export interface ObservedWorker {
  role: string;
  taskId: string;
  latest: SdlcNode;
  records: SdlcNode[];
}

/** An operation is an observed execution record, not a unique live process. */
export function observedWorkers(nodes: SdlcNode[]): ObservedWorker[] {
  const groups = new Map<string, ObservedWorker>();
  for (const node of nodes) {
    const role = String(node.data.role_instance || '');
    if (node.kind !== 'operation' || !role) continue;
    const taskId = String(node.data.task_id || '');
    const key = JSON.stringify([role, taskId]);
    const group = groups.get(key) || { role, taskId, latest: node, records: [] };
    group.records.push(node);
    groups.set(key, group);
  }
  const stamp = (node: SdlcNode) => { const value = Date.parse(String(node.data.last_event_at || '')); return Number.isNaN(value) ? -Infinity : value; };
  for (const group of groups.values()) {
    group.records.sort((a, b) => (stamp(b) - stamp(a)) || a.id.localeCompare(b.id));
    group.latest = group.records[0];
  }
  const priority = (node: SdlcNode) => ['failed', 'blocked', 'rejected', 'evidence_missing'].includes(node.status) ? 0 : ['running', 'in_progress'].includes(node.status) ? 1 : 2;
  return [...groups.values()].sort((a, b) => priority(a.latest) - priority(b.latest) || a.role.localeCompare(b.role) || a.taskId.localeCompare(b.taskId));
}

export function stageWorkers(nodes: SdlcNode[], stageId: string): ObservedWorker[] {
  return observedWorkers(nodes.filter(node => node.data.parent_stage_id === stageId));
}

/** Display only an activity name and time; never expose raw tool input on a card. */
export function workerActivity(node: SdlcNode): { label: string; time: string } | null {
  const activity = Array.isArray(node.data.recent_activity) ? node.data.recent_activity : [];
  const event = [...activity].reverse().find(item => {
    if (!item || typeof item !== "object") return false;
    const payload = (item as Record<string, unknown>).payload;
    return payload && typeof payload === "object" && Boolean((payload as Record<string, unknown>).tool_name);
  }) || activity.at(-1);
  if (!event || typeof event !== 'object') return null;
  const record = event as Record<string, unknown>;
  const payload = record.payload && typeof record.payload === 'object' ? record.payload as Record<string, unknown> : {};
  const type = String(record.type || '');
  const labels: Record<string, string> = { 'agent.usage': '用量记录', 'codex.hook.stop': '会话停止', 'codex.hook.session_start': '会话开始', 'codex.hook.user_prompt_submit': '收到输入' };
  return { label: String(payload.tool_name || labels[type] || type || '活动'), time: String(record.ts || '') };
}

/** A role node can own observed operations outside compiled stage containers. */
export function unmappedRoleOperationIds(roleId: string, nodes: SdlcNode[], edges: SdlcEdge[]): string[] {
  const stages = new Set(nodes.filter(node => node.id.startsWith('workflow:stage:')).map(node => String(node.data.stage_id || node.id.slice('workflow:stage:'.length))));
  const operations = new Set(nodes.filter(node => node.kind === 'operation' && node.data.role_instance && !stages.has(String(node.data.parent_stage_id || ''))).map(node => node.id));
  return edges.filter(edge => edge.relation === 'executes' && edge.source === roleId && operations.has(edge.target)).map(edge => edge.target);
}
