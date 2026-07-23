import "@xyflow/react/dist/style.css";

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  ChevronDown,
  ChevronRight,
  ChevronsDown,
  ChevronsUp,
  ExternalLink,
  Maximize2,
  Minimize2,
  Scan,
  Search,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { DeliveryTaskTry, DeliveryTrace } from "../../api/types";
import { GoalCoverageStatus } from "../goal-coverage/GoalCoverageStatus";
import {
  buildDeliveryWorkGraph,
  workClaimNodeId,
  workGoalNodeId,
  type DeliveryWorkGraphNode,
} from "./deliveryWorkGraphModel";
import {
  buildDeliveryWorkModel,
  type DeliveryWorkClaim,
  type DeliveryWorkTask,
} from "./deliveryWorkModel";

type WorkNodeData = Record<string, unknown> & DeliveryWorkGraphNode & {
  dimmed: boolean;
  matched: boolean;
  onToggle: (nodeId: string) => void;
};
type WorkFlowNode = Node<WorkNodeData, "work">;
type WorkFlowEdge = Edge<Record<string, never>, "smoothstep">;

const nodeTypes = { work: WorkGraphNode };
const DESKTOP_INITIAL_ZOOM = 1;
const MOBILE_INITIAL_VIEWPORT = { x: 16, y: 72, zoom: 0.55 };
const WORK_NODE_WIDTH = 240;
const WORK_NODE_HEIGHT = 102;

export function DeliveryWorkView({
  focusedClaimId,
  onSelectTask,
  trace,
}: {
  focusedClaimId?: string;
  onSelectTask?: (taskId: string) => void;
  trace: DeliveryTrace;
}) {
  const model = useMemo(() => buildDeliveryWorkModel(trace), [trace]);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(() => new Set());
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [focusNodeId, setFocusNodeId] = useState("");
  const [query, setQuery] = useState("");
  const [fullscreen, setFullscreen] = useState(false);
  const [instance, setInstance] = useState<ReactFlowInstance<WorkFlowNode, WorkFlowEdge> | null>(null);
  const isNarrow = useNarrowViewport();

  const fullGraph = useMemo(() => buildDeliveryWorkGraph(model), [model]);
  const graph = useMemo(
    () => buildDeliveryWorkGraph(model, collapsedIds),
    [collapsedIds, model],
  );
  const normalizedQuery = query.trim().toLowerCase();
  const matchIds = useMemo(() => new Set(
    normalizedQuery
      ? fullGraph.nodes.filter((node) => node.searchText.includes(normalizedQuery)).map((node) => node.id)
      : [],
  ), [fullGraph.nodes, normalizedQuery]);

  const toggleNode = (nodeId: string) => {
    setCollapsedIds((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  };

  useEffect(() => {
    if (!focusedClaimId) return;
    const claimNodeId = workClaimNodeId(focusedClaimId);
    setCollapsedIds((current) => {
      const next = new Set(current);
      next.delete(workGoalNodeId(model));
      next.delete(claimNodeId);
      return next;
    });
    setSelectedNodeId(claimNodeId);
    setFocusNodeId(claimNodeId);
  }, [focusedClaimId]);

  useEffect(() => {
    if (!normalizedQuery || !matchIds.size) return;
    const firstMatch = fullGraph.nodes.find((node) => matchIds.has(node.id));
    if (!firstMatch) return;
    setCollapsedIds((current) => {
      const next = new Set(current);
      next.delete(workGoalNodeId(model));
      if (firstMatch.kind === "task" && firstMatch.claimId) {
        next.delete(workClaimNodeId(firstMatch.claimId));
      } else if (firstMatch.kind === "task") {
        next.delete("unmapped:work");
      }
      if (firstMatch.kind === "claim" || firstMatch.kind === "unmapped") {
        next.delete(firstMatch.id);
      }
      return next;
    });
    setSelectedNodeId(firstMatch.id);
    setFocusNodeId(firstMatch.id);
  }, [fullGraph.nodes, matchIds, model, normalizedQuery]);

  useEffect(() => {
    if (!instance || !focusNodeId || !graph.nodes.some((node) => node.id === focusNodeId)) return;
    const frame = window.requestAnimationFrame(() => {
      void instance.fitView({
        duration: 260,
        maxZoom: 1.1,
        nodes: [{ id: focusNodeId }],
        padding: 0.8,
      });
      setFocusNodeId("");
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focusNodeId, graph.nodes, instance]);

  const flowNodes = useMemo<WorkFlowNode[]>(() => graph.nodes.map((node) => ({
    id: node.id,
    type: "work",
    position: node.position,
    data: {
      ...node,
      dimmed: Boolean(normalizedQuery) && !matchIds.has(node.id),
      matched: matchIds.has(node.id),
      onToggle: toggleNode,
    },
    draggable: false,
    selectable: true,
    selected: selectedNodeId === node.id,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    zIndex: selectedNodeId === node.id ? 3 : 1,
  })), [graph.nodes, matchIds, normalizedQuery, selectedNodeId]);

  const flowEdges = useMemo<WorkFlowEdge[]>(() => graph.edges.map((edge) => {
    const secondary = edge.kind === "secondary";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      label: secondary ? "also covers" : undefined,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: secondary ? "var(--text-tertiary)" : "var(--brand)",
        width: 14,
        height: 14,
      },
      style: {
        stroke: secondary ? "var(--text-tertiary)" : "var(--brand)",
        strokeDasharray: secondary ? "5 5" : undefined,
        strokeOpacity: secondary ? 0.62 : 0.44,
        strokeWidth: secondary ? 1.25 : 1.5,
      },
      labelBgStyle: { fill: "var(--panel)", fillOpacity: 0.9 },
      labelStyle: { fill: "var(--text-tertiary)" },
      zIndex: secondary ? 0 : 1,
    };
  }), [graph.edges]);

  const selectedGraphNode = fullGraph.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const selectedClaim = selectedGraphNode?.claimId
    ? model.claims.find((claim) => claim.claim.goal_claim_id === selectedGraphNode.claimId) ?? null
    : null;
  const selectedTask = selectedGraphNode?.taskId
    ? model.tasks.find((task) => task.taskId === selectedGraphNode.taskId) ?? null
    : null;

  useEffect(() => {
    if (!instance || focusedClaimId) return;
    let secondFrame = 0;
    const firstFrame = window.requestAnimationFrame(() => {
      secondFrame = window.requestAnimationFrame(() => {
        if (isNarrow) {
          void instance.setViewport(MOBILE_INITIAL_VIEWPORT, { duration: 220 });
          return;
        }
        const goalNode = fullGraph.nodes.find((node) => node.kind === "goal");
        if (!goalNode) return;
        const claimNode = fullGraph.nodes.find((node) => (
          node.kind === "claim" || node.kind === "unmapped"
        ));
        const anchorNode = claimNode ?? goalNode;
        void instance.setCenter(
          anchorNode.position.x + WORK_NODE_WIDTH / 2,
          goalNode.position.y + WORK_NODE_HEIGHT / 2,
          { duration: 240, zoom: DESKTOP_INITIAL_ZOOM },
        );
      });
    });
    return () => {
      window.cancelAnimationFrame(firstFrame);
      if (secondFrame) window.cancelAnimationFrame(secondFrame);
    };
  }, [focusedClaimId, instance, isNarrow, trace.feature_id]);

  useEffect(() => {
    if (!fullscreen) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [fullscreen]);

  return (
    <section
      className={`delivery-map-work delivery-work${fullscreen ? " is-focus" : ""}`}
      data-testid="delivery-map-work"
    >
      <header className="delivery-work-toolbar">
        <div className="delivery-work-summary" data-testid="delivery-work-summary">
          <WorkMetric label="Tasks" value={model.summary.total} />
          <WorkMetric label="Done" value={model.summary.done} />
          <WorkMetric label="Running" value={model.summary.running} />
          <WorkMetric label="Blocked" value={model.summary.blocked} />
          <WorkMetric label="Verified" value={model.summary.verified} />
        </div>
        <div className="delivery-work-tools">
          <label className="delivery-work-search">
            <Search aria-hidden="true" size={14} />
            <input
              aria-label="Search Work tree"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search goal, claim, or task"
              value={query}
            />
            {query ? <small>{matchIds.size}</small> : null}
          </label>
          <button
            aria-label="Expand all Work branches"
            className="icon-button"
            onClick={() => setCollapsedIds(new Set())}
            title="Expand all"
            type="button"
          >
            <ChevronsDown aria-hidden="true" size={15} />
          </button>
          <button
            aria-label="Collapse Work task branches"
            className="icon-button"
            onClick={() => setCollapsedIds(new Set([
              ...model.claims.map((claim) => workClaimNodeId(claim.claim.goal_claim_id)),
              ...(model.unclaimedTasks.length ? ["unmapped:work"] : []),
            ]))}
            title="Collapse task branches"
            type="button"
          >
            <ChevronsUp aria-hidden="true" size={15} />
          </button>
          <button
            aria-label="Fit Work tree"
            className="icon-button"
            onClick={() => void instance?.fitView({ duration: 260, maxZoom: 1, padding: 0.16 })}
            title="Fit tree"
            type="button"
          >
            <Scan aria-hidden="true" size={15} />
          </button>
          <button
            aria-label={fullscreen ? "Exit Work fullscreen" : "Enter Work fullscreen"}
            className="icon-button"
            onClick={() => setFullscreen((current) => !current)}
            title={fullscreen ? "Restore Work" : "Fullscreen Work"}
            type="button"
          >
            {fullscreen
              ? <Minimize2 aria-hidden="true" size={15} />
              : <Maximize2 aria-hidden="true" size={15} />}
          </button>
        </div>
      </header>

      <div className={`delivery-work-graph-shell${selectedGraphNode ? " has-inspector" : ""}`}>
        <div className="delivery-work-canvas" data-testid="delivery-work-canvas">
          <ReactFlow<WorkFlowNode, WorkFlowEdge>
            edges={flowEdges}
            elementsSelectable
            maxZoom={1.6}
            minZoom={isNarrow ? 0.35 : 0.2}
            nodes={flowNodes}
            nodesConnectable={false}
            nodesDraggable={false}
            nodeTypes={nodeTypes}
            onInit={setInstance}
            onNodeClick={(_, node) => setSelectedNodeId(node.id)}
            onPaneClick={() => setSelectedNodeId("")}
            panOnDrag
            proOptions={{ hideAttribution: true }}
            zoomOnDoubleClick={false}
          >
            <Background color="var(--line-soft)" gap={22} size={1} variant={BackgroundVariant.Dots} />
            <Controls position="bottom-left" showInteractive={false} />
          </ReactFlow>
        </div>
        {selectedGraphNode ? (
          <WorkInspector
            claim={selectedClaim}
            graphNode={selectedGraphNode}
            model={model}
            onClose={() => setSelectedNodeId("")}
            onSelectTask={onSelectTask}
            task={selectedTask}
          />
        ) : null}
      </div>
    </section>
  );
}

function WorkGraphNode({ data, selected }: NodeProps<WorkFlowNode>) {
  const expandable = data.childCount > 0 && data.kind !== "task";
  return (
    <article
      className={`delivery-work-graph-node is-${data.kind}${selected ? " is-selected" : ""}${data.matched ? " is-match" : ""}${data.dimmed ? " is-dimmed" : ""}`}
      data-testid="delivery-work-node"
      data-work-kind={data.kind}
      data-work-node-id={data.id}
    >
      {data.kind !== "goal" ? <Handle isConnectable={false} position={Position.Left} type="target" /> : null}
      <header>
        <span>{data.kind === "unmapped" ? "Unmapped" : data.kind}</span>
        <GoalCoverageStatus label={data.status} status={data.status} />
      </header>
      <strong title={data.title}>{data.title}</strong>
      <small className="mono" title={data.reference}>{data.reference}</small>
      <footer>
        {data.kind === "task" ? <span title={data.owner}>{data.owner}</span> : <span>{data.owner}</span>}
        {data.kind === "task" ? <span>{data.implementation}</span> : <span>{data.verification}</span>}
      </footer>
      {expandable ? (
        <button
          aria-label={`${data.collapsed ? "Expand" : "Collapse"} ${data.kind} ${data.reference}`}
          className="delivery-work-node-toggle nodrag nopan"
          onClick={(event) => {
            event.stopPropagation();
            data.onToggle(data.id);
          }}
          title={data.collapsed ? "Expand" : "Collapse"}
          type="button"
        >
          {data.collapsed ? <ChevronRight aria-hidden="true" size={14} /> : <ChevronDown aria-hidden="true" size={14} />}
          <span>{data.childCount}</span>
        </button>
      ) : null}
      {data.kind !== "task" ? <Handle isConnectable={false} position={Position.Right} type="source" /> : null}
    </article>
  );
}

function WorkMetric({ label, value }: { label: string; value: number }) {
  return <span><small>{label}</small><strong>{value}</strong></span>;
}

function WorkInspector({
  claim,
  graphNode,
  model,
  onClose,
  onSelectTask,
  task,
}: {
  claim: DeliveryWorkClaim | null;
  graphNode: DeliveryWorkGraphNode;
  model: ReturnType<typeof buildDeliveryWorkModel>;
  onClose: () => void;
  onSelectTask?: (taskId: string) => void;
  task: DeliveryWorkTask | null;
}) {
  return (
    <aside className="delivery-work-inspector" data-testid="delivery-work-inspector">
      <header className="delivery-work-inspector-head">
        <div>
          <span>{graphNode.kind}</span>
          <h3>{graphNode.title}</h3>
          <small className="mono">{graphNode.reference}</small>
        </div>
        <button aria-label="Close Work inspector" className="icon-button" onClick={onClose} type="button">
          <X aria-hidden="true" size={15} />
        </button>
      </header>
      {task ? (
        <TaskInspector onSelectTask={onSelectTask} task={task} />
      ) : claim ? (
        <ClaimInspector claim={claim} />
      ) : (
        <GoalInspector model={model} />
      )}
    </aside>
  );
}

function GoalInspector({ model }: { model: ReturnType<typeof buildDeliveryWorkModel> }) {
  return (
    <>
      <InspectorGroup label="Delivery">
        <InspectorRow label="Status"><GoalCoverageStatus label={model.goal?.status || "unknown"} status={model.goal?.status} /></InspectorRow>
        <InspectorRow label="Tasks"><span>{model.summary.total}</span></InspectorRow>
        <InspectorRow label="Implementation"><span>{model.summary.done}/{model.summary.total} done</span></InspectorRow>
        <InspectorRow label="Verification"><span>{model.summary.verified}/{model.summary.total} verified</span></InspectorRow>
      </InspectorGroup>
      <InspectorGroup label="Claims">
        {model.claims.map((claim) => (
          <span className="delivery-work-inspector-ref" key={claim.claim.goal_claim_id}>
            <span>{claim.claim.title}</span>
            <GoalCoverageStatus label={claim.claim.closure || "unknown"} status={claim.claim.closure} />
          </span>
        ))}
      </InspectorGroup>
    </>
  );
}

function ClaimInspector({ claim }: { claim: DeliveryWorkClaim }) {
  const taskCount = claim.tasks.length + claim.linkedTasks.length;
  return (
    <>
      <InspectorGroup label="Coverage">
        <InspectorRow label="Plan"><GoalCoverageStatus label={claim.claim.plan_coverage || "uncovered"} status={claim.claim.plan_coverage} /></InspectorRow>
        <InspectorRow label="Implementation"><GoalCoverageStatus label={claim.claim.execution || "pending"} status={claim.claim.execution} /></InspectorRow>
        <InspectorRow label="Verification"><GoalCoverageStatus label={claim.claim.task_verification || "unverified"} status={claim.claim.task_verification} /></InspectorRow>
        <InspectorRow label="Closure"><GoalCoverageStatus label={claim.claim.closure || "unknown"} status={claim.claim.closure} /></InspectorRow>
      </InspectorGroup>
      <InspectorGroup label="Source">
        <span className="mono delivery-work-ref">{claim.claim.source_ref || "No source ref"}</span>
        <span>{claim.claim.mandatory === false ? "Optional" : "Mandatory"}</span>
      </InspectorGroup>
      <InspectorGroup label={`Tasks · ${taskCount}`}>
        {taskCount ? [...claim.tasks, ...claim.linkedTasks].map((task) => (
          <span className="delivery-work-inspector-ref" key={task.taskId}>
            <span>{task.title}</span>
            <GoalCoverageStatus label={task.status} status={task.status} />
          </span>
        )) : <span className="delivery-work-missing">No covering task · needs plan</span>}
      </InspectorGroup>
      {claim.claim.gap_refs?.length ? (
        <InspectorGroup label="Open gaps">
          {claim.claim.gap_refs.map((ref) => <span className="mono delivery-work-ref" key={ref}>{ref}</span>)}
        </InspectorGroup>
      ) : null}
    </>
  );
}

function TaskInspector({
  onSelectTask,
  task,
}: {
  onSelectTask?: (taskId: string) => void;
  task: DeliveryWorkTask;
}) {
  const hasEvidence = Boolean(
    task.blockedBy.length || task.tries.length || task.results.length || task.evidenceRefs.length,
  );
  return (
    <>
      <InspectorGroup label="Task">
        <InspectorRow label="Status"><GoalCoverageStatus label={task.status} status={task.status} /></InspectorRow>
        <InspectorRow label="Owner"><span>{task.owner}</span></InspectorRow>
        <InspectorRow label="Claims"><span>{task.claimIds.length ? task.claimIds.join(", ") : "unmapped"}</span></InspectorRow>
        {task.blockedBy.length ? <InspectorRow label="Blocked by"><span className="mono">{task.blockedBy.join(", ")}</span></InspectorRow> : null}
        {onSelectTask ? (
          <button className="delivery-work-open-task" onClick={() => onSelectTask(task.taskId)} type="button">
            <ExternalLink aria-hidden="true" size={14} />
            <span>Open canonical task</span>
          </button>
        ) : null}
      </InspectorGroup>
      {task.tries.length ? (
        <InspectorGroup label="Implementation">
          {task.tries.map((tryItem) => <TryRow key={`${task.taskId}:${tryItem.try}`} tryItem={tryItem} />)}
        </InspectorGroup>
      ) : null}
      {task.results.length ? (
        <InspectorGroup label="Verification">
          {task.results.map((result) => (
            <div className="delivery-work-result" key={result.node_id}>
              <span><strong>{result.title}</strong><small className="mono">{result.result_ref || result.node_id}</small></span>
              <GoalCoverageStatus label={result.status || "unverified"} status={result.status} />
            </div>
          ))}
        </InspectorGroup>
      ) : null}
      {task.evidenceRefs.length ? (
        <InspectorGroup label="Evidence">
          {task.evidenceRefs.slice(0, 6).map((ref) => <span className="mono delivery-work-ref" key={ref}>{ref}</span>)}
        </InspectorGroup>
      ) : null}
      {!hasEvidence ? (
        <InspectorGroup label="Activity">
          <span className="muted">No implementation attempt or verification result recorded.</span>
        </InspectorGroup>
      ) : null}
    </>
  );
}

function TryRow({ tryItem }: { tryItem: DeliveryTaskTry }) {
  const gates = tryItem.gate_results ?? [];
  return (
    <div className="delivery-work-try" data-testid="delivery-work-try">
      <span>
        <strong>Try #{tryItem.try}</strong>
        {tryItem.rework_kind ? <small>{tryItem.rework_kind}</small> : null}
      </span>
      <GoalCoverageStatus label={tryItem.outcome} status={tryItem.outcome} />
      <span className="delivery-work-gates">
        {gates.length ? gates.map((gate) => (
          <small key={`${gate.type}:${gate.event_id || ""}`}>{gate.type} {gate.passed ? "passed" : "failed"}</small>
        )) : <small>no gate result</small>}
      </span>
    </div>
  );
}

function InspectorGroup({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <section className="delivery-work-inspector-group">
      <h4>{label}</h4>
      <div>{children}</div>
    </section>
  );
}

function InspectorRow({ children, label }: { children: React.ReactNode; label: string }) {
  return <div className="delivery-work-inspector-row"><span>{label}</span><div>{children}</div></div>;
}

function useNarrowViewport(): boolean {
  const [isNarrow, setIsNarrow] = useState(() => (
    typeof window !== "undefined" && window.matchMedia("(max-width: 760px)").matches
  ));
  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const update = () => setIsNarrow(media.matches);
    media.addEventListener("change", update);
    update();
    return () => media.removeEventListener("change", update);
  }, []);
  return isNarrow;
}
