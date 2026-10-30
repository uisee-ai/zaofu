import { asRecord, asRecordArray } from '../../app/shared';
import type { SdlcEdge, SdlcNode } from './sdlcGraphModel';

function activityDescription(event: Record<string, unknown>): { title: string; detail: string } {
  const payload = asRecord(event.payload);
  const type = String(event.type || '');
  const tool = typeof payload.tool_name === 'string' ? payload.tool_name : '';
  const input = asRecord(payload.tool_input);
  const detail = [input.file_path, input.path, input.command, payload.message, payload.reason]
    .find(value => typeof value === 'string' && value.trim());
  const title = tool ? `${type.includes('post_tool_use') ? '工具返回' : '调用工具'}：${tool}` :
    ({ 'agent.usage': '用量记录', 'codex.hook.stop': '会话停止', 'codex.hook.session_start': '会话开始', 'codex.hook.user_prompt_submit': '收到输入' } as Record<string, string>)[type] || type || '活动';
  return { title, detail: typeof detail === 'string' ? detail.slice(0, 240) : '' };
}

export default function WorkflowOperationDetails({ node, projectId, taskNode, nodes, edges, onTask, onOperation }: { node: SdlcNode; projectId: string; taskNode?: SdlcNode; nodes: SdlcNode[]; edges: SdlcEdge[]; onTask: (id: string) => void; onOperation: (id: string) => void }) {
  const binding = asRecord(node.data.agent_binding);
  const source = binding.source === 'provider_usage' ? '实际用量记录' : binding.source === 'config_snapshot' ? '配置快照' : '来源未知';
  const activities = asRecordArray(node.data.recent_activity);
  const traceId = typeof node.data.trace_id === 'string' ? node.data.trace_id.trim() : '';
  const traceParams = new URLSearchParams({ project: projectId, page: 'traces', ...(traceId ? { trace_id: traceId } : {}) });
  const related = edges.filter(edge => edge.relation === 'delegates' && (edge.source === node.id || edge.target === node.id))
    .map(edge => ({ edge, other: nodes.find(item => item.kind === 'operation' && item.id === (edge.source === node.id ? edge.target : edge.source)) }))
    .filter((item): item is { edge: SdlcEdge; other: SdlcNode } => Boolean(item.other));
  return <section aria-label="Worker 执行详情">
    <p>Agent：{String(node.data.role_instance || '未绑定')} · Attempt：{String(node.data.active_attempt_id || '未观测')}</p>
    <p>Task：{taskNode?.label || String(node.data.task_id || '未关联')}</p>
    <p>Provider：{String(binding.backend || '未观测')} · 模型：{String(binding.model || '未观测')} · {source}</p>
    {taskNode && <button type="button" onClick={() => onTask(taskNode.id)}>在任务 Graph 查看 Task</button>}
    <p><a href={`/?${traceParams}`}>{traceId ? '查看关联 Trace' : '查看 Traces 列表'}</a>{!traceId && ' · 当前执行记录未提供 Trace ID'}</p>
    {related.length > 0 && <><h4>已记录的父子执行</h4>{related.map(({ edge, other }) => <button type="button" key={edge.id} onClick={() => onOperation(other.id)}>{edge.target === node.id ? '父执行' : '子执行'} · {other.label}</button>)}</>}
    <h4>活动轨迹</h4>
    <p className="sdlc-activity-hint">显示此执行记录关联的最近 {activities.length} 条已观测活动。</p>
    {!activities.length && <p>活动未观测。</p>}
    <ol className="sdlc-activity-list">{activities.map((event, index) => {
      const item = activityDescription(event);
      return <li key={`${String(event.id || '')}:${index}`}><time>{String(event.ts || '时间未观测')}</time><strong>{item.title}</strong>{item.detail && <small>{item.detail}</small>}</li>;
    })}</ol>
  </section>;
}
