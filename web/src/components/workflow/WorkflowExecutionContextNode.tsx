import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { statusLabels, type SdlcNode } from './sdlcGraphModel';
import { observedRunStatus } from './executionContextModel';

type ContextNode = Node<{ node: SdlcNode }, 'executionContext'>;

export function WorkflowExecutionContextNode({ data }: NodeProps<ContextNode>) {
  const node = data.node;
  const kind = String(node.data.context_kind || '');
  const isRun = kind === 'run';
  const isResult = kind === 'result';
  const pendingRun = isRun && (node.kind === 'preparation' || node.data.context_placeholder);
  const status = pendingRun ? node.kind === 'preparation' ? '尚未启动' : '运行数据未加载' : observedRunStatus(node);
  return <div className={`execution-context execution-context-${kind}`}>
    <Handle type="target" position={Position.Left} />
    <small>{isRun ? 'Run' : isResult ? '运行结果' : node.kind === 'goal' ? 'Goal' : '需求'}</small>
    <strong title={node.label}>{isRun ? node.kind === 'preparation' ? '准备中' : 'Workflow Run' : node.label}</strong>
    {isRun && <span title={node.label}>{node.kind === 'preparation' ? '尚未启动' : node.label}</span>}
    {isRun && <em>{status}{!pendingRun && node.kind === "run" ? " · 运行控制：Kernel" : ""}</em>}
    {isResult && <span>{node.data.terminal_event_id ? '来源：内核终态事件' : '等待或缺少终态证据'}</span>}
    {kind === 'goal' && <span>{node.kind === 'goal' ? `Goal ID：${node.id.slice(5)}` : 'Goal 身份未加载或未绑定'}{!node.data.context_placeholder && node.kind !== 'goal' ? ` · ${statusLabels[node.status] || node.status}` : ''}</span>}
    <Handle type="source" position={Position.Right} />
  </div>;
}
