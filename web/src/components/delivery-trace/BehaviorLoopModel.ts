import type {
  DeliveryBehaviorOverlay,
  DeliveryEvalOverlay,
  DeliveryThickTrace,
  LoopActionRecord,
  LoopDiagnosisRecord,
  LoopLearningRecord,
  LoopItem,
  LoopProjection,
  LoopVerificationRecord,
} from "../../api/types";

export type LoopNodeKind = "trace" | "behavior" | "diagnosis" | "eval" | "improvement" | "action" | "verify" | "learn";

export interface LoopNode {
  id: string;
  kind: LoopNodeKind;
  label: string;
  status: string;
  summary: string;
  x: number;
  y: number;
  size: number;
  displayLabel?: string;
  eventIds: string[];
  taskIds: string[];
  loopId?: string;
  candidateId?: string;
  fingerprint?: string;
  latestActionId?: string;
  latestActionStatus?: string;
  latestVerificationId?: string;
  latestVerificationStatus?: string;
  suggestedAction?: string;
  sourceKind?: string;
  fixLayer?: string;
  verificationId?: string;
  learningId?: string;
  artifactKind?: string;
  artifactRef?: string;
}

export interface LoopEdge {
  id: string;
  from: string;
  to: string;
  kind: string;
  status: string;
}

export interface LoopModel {
  actions: LoopNode[];
  edges: LoopEdge[];
  nodes: LoopNode[];
  traceNodes: LoopNode[];
}

export function buildLoopModel(
  thick: DeliveryThickTrace | null,
  projection: LoopProjection | null = null,
  targetId = "",
  targetLoopId = "",
): LoopModel {
  if (!thick && !projection) return { actions: [], edges: [], nodes: [], traceNodes: [] };
  const traceNodes = (thick?.spans ?? []).slice(0, 12).map((span, index): LoopNode => ({
    id: `trace:${span.span_id}`,
    kind: "trace",
    label: span.name || span.run_id || span.span_id,
    status: span.status || "observed",
    summary: span.task_id || span.role || span.kind || "runtime span",
    x: 9 + (index % 2) * 6,
    y: 12 + index * 6.5,
    size: 42,
    eventIds: span.raw_event_refs ?? [],
    taskIds: span.task_id ? [span.task_id] : [],
  }));
  const graphTraceNodes = aggregateTraceNodes(traceNodes, thick?.spans.length ?? 0);
  const loopIds = relatedLoopIds(projection, thick, targetId, targetLoopId);
  const behaviorRows = rowsForLoops(projection?.behaviors ?? [], loopIds);
  const diagnosisRows = rowsForLoops(projection?.diagnoses ?? [], loopIds);
  const evalRows = rowsForLoops(projection?.evals ?? [], loopIds);
  const candidateRows = rowsForLoops(projection?.candidates ?? [], loopIds).slice(0, 8);
  const actionRows = projection?.actions ?? [];
  const verificationRows = rowsForLoops(projection?.verifications ?? [], loopIds);
  const learningRows = rowsForLoops(projection?.learning ?? [], loopIds);
  const behaviorNodes = behaviorRows.map((behavior, index) => behaviorNode(behavior, index));
  const diagnosisNodes = diagnosisRows.map((diagnosis, index) => diagnosisNode(diagnosis, index));
  const evalNodes = evalRows.map((item, index) => evalNode(item, index));
  const improvementNodes = candidateRows.map((item, index) => (
    improvementNode(item, index, latestActionForCandidate(actionRows, String(item.candidate_id ?? "")))
  ));
  const actionNodes = improvementNodes.map((node, index) => actionNode(node, index));
  const verificationNodes = verificationRows.map((item, index) => verificationNode(item, index));
  const learningNodes = learningRows.map((item, index) => learningNode(item, index));
  const nodes = [
    ...graphTraceNodes,
    ...behaviorNodes,
    ...diagnosisNodes,
    ...evalNodes,
    ...improvementNodes,
    ...actionNodes,
    ...verificationNodes,
    ...learningNodes,
  ];
  const edges: LoopEdge[] = [];
  connectTraceToBehaviors(edges, graphTraceNodes, behaviorNodes);
  connectTraceToDiagnoses(edges, graphTraceNodes, diagnosisNodes);
  connectDiagnoses(edges, behaviorNodes, diagnosisNodes);
  if (!behaviorNodes.length) connectTraceToEvals(edges, graphTraceNodes, evalNodes);
  connectBehaviorsToEvals(edges, behaviorNodes, evalNodes);
  connectToImprovements(edges, [...diagnosisNodes, ...behaviorNodes, ...evalNodes], improvementNodes);
  for (const improvement of improvementNodes) {
    const target = actionNodes.find((node) => node.sourceKind === improvement.sourceKind) ?? actionNodes[0];
    if (target) edges.push(edge(improvement.id, target.id, "suggests", improvement.status));
  }
  connectActionsToVerify(edges, actionNodes, verificationNodes);
  connectVerifyToLearning(edges, verificationNodes, learningNodes);
  return { actions: actionNodes, edges, nodes, traceNodes };
}

function aggregateTraceNodes(traceNodes: LoopNode[], totalSpanCount: number): LoopNode[] {
  if (traceNodes.length <= 1) return traceNodes;
  const eventIds = unique(traceNodes.flatMap((node) => node.eventIds));
  const taskIds = unique(traceNodes.flatMap((node) => node.taskIds));
  const failed = traceNodes.filter((node) => node.status === "failed").length;
  const running = traceNodes.filter((node) => node.status === "running").length;
  const visibleCount = traceNodes.length;
  const suffix = totalSpanCount > visibleCount ? `/${totalSpanCount}` : "";
  return [{
    id: "trace:aggregate",
    kind: "trace",
    label: "Trace evidence",
    status: failed ? "failed" : running ? "running" : "observed",
    summary: failed ? `${failed} failed / ${visibleCount}${suffix} spans` : `${visibleCount}${suffix} spans`,
    x: 12,
    y: 50,
    size: 84,
    eventIds,
    taskIds,
  }];
}

export function defaultActions(): LoopNode[] {
  return ["Backlog", "Autoresearch", "Supervisor", "Replan"].map((label, index) => ({
    id: `action:default:${label}`,
    kind: "action",
    label,
    status: "candidate",
    summary: `${label.toLowerCase()} candidate`,
    x: 92,
    y: 20 + index * 12,
    size: 58,
    eventIds: [],
    taskIds: [],
  }));
}

export function filterLoopsForTarget(loops: LoopItem[], featureId: string, relatedLoopIds: string[] = []): LoopItem[] {
  const related = new Set(relatedLoopIds);
  if (related.size) {
    const directRelated = loops.filter((loop) => related.has(loop.loop_id));
    if (directRelated.length) return directRelated;
  }
  if (!featureId) return loops;
  const direct = loops.filter((loop) => (loop.feature_ids ?? []).includes(featureId));
  return direct.length ? direct : loops;
}

export function highlightSet(model: LoopModel, selectedId: string): Set<string> {
  const out = new Set<string>();
  if (!selectedId) return out;
  out.add(selectedId);
  for (const item of model.edges) {
    if (item.from === selectedId || item.to === selectedId) {
      out.add(item.id);
      out.add(item.from);
      out.add(item.to);
    }
  }
  return out;
}

export function nodeLabel(node: LoopNode): string {
  if (node.displayLabel) return node.displayLabel;
  if (node.kind === "trace") return "Trace";
  if (node.kind === "behavior") return "Signal";
  if (node.kind === "diagnosis") return "Diagnose";
  if (node.kind === "eval") return "Eval";
  if (node.kind === "improvement") return "Improve";
  if (node.kind === "action") return "Act";
  if (node.kind === "verify") return "Gate";
  if (node.kind === "learn") return "Learn";
  return humanizeLabel(node.label);
}

function behaviorNode(behavior: DeliveryBehaviorOverlay, index: number): LoopNode {
  return {
    id: `behavior:${behavior.behavior_id}`,
    kind: "behavior",
    label: behavior.kind,
    status: behavior.status || "observed",
    summary: behavior.summary || behavior.detector || behavior.owner_event_type || "behavior",
    x: 36 + (index % 2) * 4,
    y: 20 + index * 14,
    size: behavior.status === "failed" ? 88 : 64,
    eventIds: behavior.event_ids ?? [],
    taskIds: behavior.task_ids ?? [],
    loopId: behavior.loop_id,
    sourceKind: behavior.kind,
  };
}

function diagnosisNode(diagnosis: LoopDiagnosisRecord, index: number): LoopNode {
  return {
    id: `diagnosis:${diagnosis.diagnosis_id}`,
    kind: "diagnosis",
    label: diagnosis.fix_layer || diagnosis.source_kind || "diagnosis",
    status: diagnosis.confidence && diagnosis.confidence >= 0.8 ? "passed" : "warn",
    summary: diagnosis.reason || diagnosis.recommended_action || "diagnosis",
    x: 45 + (index % 2) * 4,
    y: 18 + index * 13,
    size: 76,
    eventIds: diagnosis.evidence_refs ?? [],
    taskIds: stringList((diagnosis.evidence_packet ?? {}).task_refs),
    loopId: diagnosis.loop_id,
    candidateId: diagnosis.candidate_id,
    sourceKind: diagnosis.source_kind,
    fixLayer: diagnosis.fix_layer,
    suggestedAction: diagnosis.recommended_action,
  };
}

function evalNode(item: DeliveryEvalOverlay, index: number): LoopNode {
  return {
    id: `eval:${item.eval_id}`,
    kind: "eval",
    label: item.kind,
    status: item.status || "observed",
    summary: item.score == null ? (item.evaluator || item.owner_event_type || "eval") : `${Math.round(item.score * 100)}% score`,
    x: 58 + (index % 2) * 4,
    y: 18 + index * 15,
    size: item.status === "failed" ? 92 : 64,
    eventIds: item.event_ids ?? [],
    taskIds: item.task_ids ?? [],
    loopId: item.loop_id,
    sourceKind: item.kind,
  };
}

function improvementNode(item: Record<string, unknown>, index: number, latestAction?: LoopActionRecord): LoopNode {
  const sourceKind = String(item.source_kind ?? item.kind ?? "improvement");
  const suggestedAction = String(item.suggested_action ?? "");
  return {
    id: `improvement:${String(item.candidate_id ?? item.fingerprint ?? index)}`,
    kind: "improvement",
    label: sourceKind,
    status: String(item.status ?? "candidate"),
    summary: String(item.summary ?? "improvement candidate"),
    x: 72 + (index % 2) * 4,
    y: 24 + index * 12,
    size: 72,
    eventIds: stringList(item.event_ids),
    taskIds: stringList(item.task_ids),
    loopId: String(item.loop_id ?? ""),
    candidateId: String(item.candidate_id ?? ""),
    fingerprint: String(item.fingerprint ?? ""),
    latestActionId: latestAction?.action_id,
    latestActionStatus: latestAction?.status,
    latestVerificationId: String(item.latest_verification_id ?? latestAction?.latest_verification_id ?? ""),
    latestVerificationStatus: String(item.latest_verification_status ?? latestAction?.latest_verification_status ?? ""),
    suggestedAction,
    sourceKind,
    fixLayer: String(item.fix_layer ?? ""),
  };
}

function actionNode(source: LoopNode, index: number): LoopNode {
  const label = actionLabel(source.suggestedAction || source.sourceKind || "inspect");
  return {
    id: `action:${source.candidateId || source.loopId || source.id}:${source.suggestedAction || index}`,
    kind: "action",
    label,
    status: source.latestActionStatus || "candidate",
    summary: source.suggestedAction || "inspect candidate",
    x: 84,
    y: 20 + index * 12,
    size: 58,
    eventIds: source.eventIds,
    taskIds: source.taskIds,
    loopId: source.loopId,
    candidateId: source.candidateId,
    fingerprint: source.fingerprint,
    latestActionId: source.latestActionId,
    latestActionStatus: source.latestActionStatus,
    latestVerificationId: source.latestVerificationId,
    latestVerificationStatus: source.latestVerificationStatus,
    suggestedAction: source.suggestedAction,
    sourceKind: source.sourceKind,
    fixLayer: source.fixLayer,
  };
}

function verificationNode(item: LoopVerificationRecord, index: number): LoopNode {
  return {
    id: `verify:${item.verification_id}`,
    kind: "verify",
    label: item.status || "verify",
    status: item.status || "pending",
    summary: item.reason || item.terminal_event_type || "verification",
    x: 91,
    y: 18 + index * 12,
    size: 58,
    eventIds: item.event_ids ?? [],
    taskIds: item.task_ids ?? [],
    loopId: item.loop_id,
    candidateId: item.candidate_id,
    verificationId: item.verification_id,
    latestVerificationId: item.verification_id,
    latestVerificationStatus: item.status,
  };
}

function learningNode(item: LoopLearningRecord, index: number): LoopNode {
  return {
    id: `learn:${item.learning_id}`,
    kind: "learn",
    label: item.artifact_kind || "learn",
    status: item.status || "candidate",
    summary: item.summary || item.promotion_path || item.artifact_ref || "learning artifact",
    x: 96,
    y: 22 + index * 12,
    size: 56,
    eventIds: item.event_ids ?? [],
    taskIds: [],
    loopId: item.loop_id,
    candidateId: item.candidate_id,
    verificationId: item.verification_id,
    learningId: item.learning_id,
    artifactKind: item.artifact_kind,
    artifactRef: item.artifact_ref,
    fixLayer: item.fix_layer,
  };
}

function connectTraceToBehaviors(edges: LoopEdge[], traces: LoopNode[], behaviors: LoopNode[]) {
  for (const behavior of behaviors) {
    const source = traces.find((trace) => overlaps(trace, behavior)) ?? traces[0];
    if (source) edges.push(edge(source.id, behavior.id, "explains", behavior.status));
  }
}

function connectTraceToDiagnoses(edges: LoopEdge[], traces: LoopNode[], diagnoses: LoopNode[]) {
  for (const diagnosis of diagnoses) {
    const source = traces.find((trace) => overlaps(trace, diagnosis)) ?? traces[0];
    if (source) edges.push(edge(source.id, diagnosis.id, "diagnoses", diagnosis.status));
  }
}

function connectTraceToEvals(edges: LoopEdge[], traces: LoopNode[], evals: LoopNode[]) {
  for (const evalItem of evals) {
    const source = traces.find((trace) => overlaps(trace, evalItem)) ?? traces[0];
    if (source) edges.push(edge(source.id, evalItem.id, "checks", evalItem.status));
  }
}

function connectDiagnoses(edges: LoopEdge[], behaviors: LoopNode[], diagnoses: LoopNode[]) {
  for (const diagnosis of diagnoses) {
    const source = behaviors.find((behavior) => overlaps(behavior, diagnosis) || behavior.sourceKind === diagnosis.sourceKind) ?? behaviors[0];
    if (source) edges.push(edge(source.id, diagnosis.id, "explains", diagnosis.status));
  }
}

function connectBehaviorsToEvals(edges: LoopEdge[], behaviors: LoopNode[], evals: LoopNode[]) {
  for (const evalItem of evals) {
    const source = behaviors.find((behavior) => overlaps(behavior, evalItem)) ?? behaviors[0];
    if (source) edges.push(edge(source.id, evalItem.id, "evaluates", evalItem.status));
  }
}

function connectToImprovements(edges: LoopEdge[], sources: LoopNode[], improvements: LoopNode[]) {
  for (const improvement of improvements) {
    const source = sources.find((item) => item.sourceKind === improvement.sourceKind || overlaps(item, improvement)) ?? sources[0];
    if (source) edges.push(edge(source.id, improvement.id, "improves", improvement.status));
  }
}

function connectActionsToVerify(edges: LoopEdge[], actions: LoopNode[], verifications: LoopNode[]) {
  for (const verification of verifications) {
    const source = actions.find((action) => (
      action.latestVerificationId === verification.verificationId
      || overlaps(action, verification)
      || action.candidateId === verification.candidateId
    )) ?? actions[0];
    if (source) edges.push(edge(source.id, verification.id, "verifies", verification.status));
  }
}

function connectVerifyToLearning(edges: LoopEdge[], verifications: LoopNode[], learning: LoopNode[]) {
  for (const item of learning) {
    const source = verifications.find((verification) => (
      verification.verificationId === item.verificationId
      || overlaps(verification, item)
    )) ?? verifications[0];
    if (source) edges.push(edge(source.id, item.id, "learns", item.status));
  }
}

function edge(from: string, to: string, kind: string, status: string): LoopEdge {
  return { id: `${kind}:${from}->${to}`, from, to, kind, status };
}

function overlaps(left: LoopNode, right: LoopNode): boolean {
  return (
    Boolean(left.loopId && right.loopId && left.loopId === right.loopId) ||
    intersects(left.eventIds, right.eventIds) ||
    intersects(left.taskIds, right.taskIds)
  );
}

function intersects(left: string[], right: string[]): boolean {
  if (!left.length || !right.length) return false;
  const values = new Set(left);
  return right.some((item) => values.has(item));
}

function unique(items: string[]): string[] {
  return [...new Set(items.filter(Boolean))];
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item)).filter(Boolean);
}

function relatedLoopIds(projection: LoopProjection | null, thick: DeliveryThickTrace | null, targetId: string, targetLoopId: string): Set<string> {
  if (targetLoopId) return new Set([targetLoopId]);
  const explicit = new Set((thick?.related_loop_ids ?? []).filter(Boolean));
  if (explicit.size) return explicit;
  const taskIds = new Set<string>();
  for (const span of thick?.spans ?? []) {
    if (span.task_id) taskIds.add(span.task_id);
  }
  for (const node of thick?.graph?.nodes ?? []) {
    if (node.task_id) taskIds.add(node.task_id);
    for (const taskId of node.task_ids ?? []) taskIds.add(taskId);
  }
  const out = new Set<string>();
  for (const loop of projection?.loops ?? []) {
    if (targetId && (loop.feature_ids ?? []).includes(targetId)) out.add(loop.loop_id);
    if ((loop.task_ids ?? []).some((taskId) => taskIds.has(taskId))) out.add(loop.loop_id);
  }
  if (out.size) return out;
  for (const loop of projection?.loops ?? []) {
    const scoped =
      (loop.feature_ids ?? []).length
      || (loop.task_ids ?? []).length
      || (loop.trace_ids ?? []).length
      || (loop.fanout_ids ?? []).length;
    if (!scoped) out.add(loop.loop_id);
  }
  return out;
}

function rowsForLoops<T extends { loop_id?: string }>(rows: T[], loopIds: Set<string>): T[] {
  if (!loopIds.size) return [];
  return rows.filter((row) => row.loop_id && loopIds.has(row.loop_id));
}

function actionLabel(value: string): string {
  const cleaned = value.replace(/[_-]+/g, " ").trim();
  if (!cleaned) return "Inspect";
  return cleaned.replace(/\b\w/g, (char) => char.toUpperCase());
}

function humanizeLabel(value: string): string {
  const cleaned = value.replace(/[_-]+/g, " ").trim();
  if (!cleaned) return "Loop";
  const first = cleaned.split(/\s+/)[0] ?? "Loop";
  return first.charAt(0).toUpperCase() + first.slice(1).toLowerCase();
}

function latestActionForCandidate(actions: LoopActionRecord[], candidateId: string): LoopActionRecord | undefined {
  if (!candidateId) return undefined;
  return actions
    .filter((action) => action.candidate_id === candidateId)
    .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")))[0];
}
