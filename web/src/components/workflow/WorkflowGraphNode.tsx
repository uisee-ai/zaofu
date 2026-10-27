import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { Check, Circle, CircleAlert, LoaderCircle } from "lucide-react";
import { asRecord } from "../../app/shared";
import { activeStatus, statusLabels, type SdlcNode } from "./sdlcGraphModel";

type GraphNode = Node<{ node: SdlcNode }, "workflowCard">;

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
  return <div className={`sdlc-card sdlc-provider-${tone}${goal ? " sdlc-goal" : ""}`}>
    <Handle type="target" position={Position.Top} />
    <div className="sdlc-card-kind">{goal ? "Goal" : node.kind}</div>
    <strong title={node.label}>{node.label}</strong>
    <div className="sdlc-card-meta">
      <span className="sdlc-provider" title={provider ? binding.source === "provider_usage" ? "Observed provider" : "Configured provider" : "No provider observation"}>
        <i />{provider || String(node.data.role_instance || node.kind)}
      </span>
      <span className={`sdlc-card-status ${active ? "is-active" : success ? "is-success" : failed ? "is-failed" : ""}`} title={statusLabels[node.status] || node.status}>
        <Icon size={13} aria-hidden="true" />{node.status.replaceAll("_", " ")}
      </span>
    </div>
    <Handle type="source" position={Position.Bottom} />
  </div>;
}

export const workflowNodeTypes = { workflowCard: WorkflowGraphNode };
