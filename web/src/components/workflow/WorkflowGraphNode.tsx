import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { Check, Circle, CircleAlert, LoaderCircle } from "lucide-react";
import { asRecord } from "../../app/shared";
import { activeStatus, statusLabels, workflowNodeLabel, type SdlcNode } from "./sdlcGraphModel";

type GraphNode = Node<{ node: SdlcNode; horizontal?: boolean }, "workflowCard">;

function WorkflowGraphNode({ data }: NodeProps<GraphNode>) {
  const node = data.node;
  const binding = asRecord(node.data.agent_binding);
  const provider = String(binding.backend || "");
  const family = provider.replace(/-headless$/, "").replace(/-code$/, "");
  const tone = ["codex", "claude", "opencode"].includes(family) ? family : "neutral";
  const active = activeStatus(node.status);
  const success = ["done", "accepted", "approved", "closed", "passed", "completed"].includes(node.status);
  const failed = ["failed", "blocked", "rejected", "evidence_missing"].includes(node.status);
  const Icon = active ? LoaderCircle : success ? Check : failed ? CircleAlert : Circle;
  const goal = node.kind === "requirement";
  const gate = node.id === "workflow:terminal:done";
  const stage = node.id.startsWith("workflow:stage:");
  return <div className={`sdlc-card sdlc-provider-${tone}${goal ? " sdlc-goal" : ""}${gate ? " sdlc-gate" : ""}`}>
    <Handle type="target" position={data.horizontal ? Position.Left : Position.Top} />
    {!gate && !stage && <div className="sdlc-card-kind">{goal ? "Goal" : node.kind}</div>}
    <strong title={node.label}>{workflowNodeLabel(node)}</strong>
    {stage && Array.isArray(node.data.roles) && node.data.roles.length > 0 && <small className="sdlc-card-summary">{node.data.roles.length} 个配置角色</small>}
    <div className="sdlc-card-meta">
      <span className="sdlc-provider" title={provider ? binding.source === "provider_usage" ? "Observed provider" : "Configured provider" : "No provider observation"}>
        {provider || node.data.role_instance ? <><i />{provider || String(node.data.role_instance)}</> : null}
      </span>
      <span className={`sdlc-card-status ${active ? "is-active" : success ? "is-success" : failed ? "is-failed" : ""}`} title={statusLabels[node.status] || node.status}>
        <Icon size={13} aria-hidden="true" />{stage || gate ? statusLabels[node.status] || node.status : node.status.replaceAll("_", " ")}
      </span>
    </div>
    <Handle type="source" position={data.horizontal ? Position.Right : Position.Bottom} />
  </div>;
}

import { WorkflowExecutionStageNode } from "./WorkflowExecutionStageNode";
import { WorkflowExecutionContextNode } from "./WorkflowExecutionContextNode";

export const workflowNodeTypes = { workflowCard: WorkflowGraphNode, executionStage: WorkflowExecutionStageNode, executionContext: WorkflowExecutionContextNode };
