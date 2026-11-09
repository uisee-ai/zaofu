import {
  diagnosticSeverity,
  readinessPresentation,
  preparationFeedback,
  stageLevels,
  stagesForCurrentFlow,
  workflowViewForRequest,
} from "../src/components/workflow/workflowProposalModel.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function testLifecycleBuckets(): void {
  assert(workflowViewForRequest({ status: "preparation_failed" }) === "decision", "failed preparation needs repair");
  assert(workflowViewForRequest({ status: "synthesis_queued" }) === "active", "queued preparation is in progress");
  assert(preparationFeedback("synthesis_queued", true).includes("not ready yet"), "enqueue must not claim a proposal exists");
  assert(preparationFeedback("proposed", true) === "Proposal prepared.", "materialized proposals are ready");
  assert(readinessPresentation({ blockerCount: 0, requestStatus: "preparation_failed", runStatus: "", terminal: "" }).tone === "error", "failed operation cannot look ready");
  assert(workflowViewForRequest({ status: "proposed" }) === "decision", "proposals need a decision");
  assert(workflowViewForRequest({ status: "clarifying" }) === "decision", "clarification needs a decision");
  assert(workflowViewForRequest({ status: "running" }) === "active", "running workflows are active");
  assert(workflowViewForRequest({ status: "rejected" }) === "history", "rejected workflows are history");
  assert(workflowViewForRequest({ status: "cancelled" }) === "history", "cancelled preparation belongs to history");
}

function testRequestReadinessPrecedesProposalReadiness(): void {
  assert(
    readinessPresentation({ blockerCount: 0, requestStatus: "cancelled", runStatus: "", terminal: "" }).title === "Workflow cancelled",
    "cancelled preparation must not be presented as ready to run",
  );
  assert(
    readinessPresentation({
      blockerCount: 0,
      requestStatus: "clarifying",
      runStatus: "",
      terminal: "",
    }).title === "Needs clarification",
    "clarifying requests must not be presented as runnable",
  );
  assert(
    readinessPresentation({
      blockerCount: 0,
      requestStatus: "ready",
      runStatus: "",
      terminal: "",
    }).title === "Ready to prepare proposal",
    "ready requirements still need a proposal",
  );
}

function testSeverityPreservesInfo(): void {
  assert(diagnosticSeverity({ severity: "INFO" }) === "INFO", "INFO must not render as a warning");
  assert(diagnosticSeverity({ severity: "WARN" }) === "WARN", "WARN must remain actionable");
  assert(diagnosticSeverity({ severity: "STOP" }) === "STOP", "STOP must remain blocking");
}

function testCurrentFlowUsesDeclaredTasks(): void {
  const stages = [
    { id: "prd-scan" },
    { id: "scope" },
    { id: "collect-a", dependencies: ["scope"] },
    { id: "collect-b", dependencies: ["scope"] },
    { id: "synthesize", dependencies: ["collect-a", "collect-b"] },
  ];
  const flowSpec = {
    documents: [{
      kind: "Workflow",
      spec: {
        tasks: [
          { name: "scope" },
          { name: "collect-a" },
          { name: "collect-b" },
          { name: "synthesize" },
        ],
      },
    }],
  };
  const selected = stagesForCurrentFlow(stages, flowSpec, "workflow", "Workflow");
  assert(selected.length === 4, "the default graph should only show the current flow");
  assert(!selected.some((stage) => stage.id === "prd-scan"), "unrelated flow stages must stay advanced");
  const levels = stageLevels(selected);
  assert(levels.length === 3, "dependency levels should preserve fanout and join");
  assert(levels[1]?.length === 2, "parallel collectors should share one phase");
  assert(levels[2]?.[0]?.id === "synthesize", "the join should follow both collectors");

  const compactSnapshot = {
    generic_workflow_spec: {
      tasks: [
        { name: "scope" },
        { name: "collect-a" },
        { name: "collect-b" },
        { name: "synthesize" },
      ],
    },
  };
  const compactSelected = stagesForCurrentFlow(
    stages,
    compactSnapshot,
    "workflow",
    "Workflow",
  );
  assert(compactSelected.length === 4, "compact workflow snapshots must select declared tasks");
}

testLifecycleBuckets();
testRequestReadinessPrecedesProposalReadiness();
testSeverityPreservesInfo();
testCurrentFlowUsesDeclaredTasks();
