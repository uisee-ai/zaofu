import type { MeasureLoopProjection } from "../../api/types";
import type { LoopEdge, LoopModel, LoopNode, LoopNodeKind } from "./BehaviorLoopModel";

export function buildMeasureGraphModel(projection: MeasureLoopProjection | null): LoopModel | null {
  if (!projection) return null;
  const stages = projection.stages ?? [];
  const stageById = new Map(stages.map((stage) => [stage.id, stage]));
  const rawNodes = (projection.graph?.nodes ?? []).length
    ? projection.graph.nodes
    : stages.map((stage) => ({ id: stage.id, label: stage.label, status: stage.tone, value: stage.value }));
  if (!rawNodes.length) return null;

  const nodes = rawNodes.map((raw, index): LoopNode => {
    const rawId = stringField(raw, "id") || `stage:${index}`;
    const stage = stageById.get(rawId);
    const label = stringField(raw, "label") || stage?.label || humanize(rawId);
    const status = stringField(raw, "status") || stage?.tone || "projection";
    const value = stringField(raw, "value") || stage?.value || "";
    const detail = stage?.detail || "";
    return {
      id: rawId,
      kind: stageKind(projection.active_lens, rawId, label),
      label,
      displayLabel: compactLabel(label),
      status,
      summary: [value, detail].filter(Boolean).join(" · ") || "projection stage",
      x: 50,
      y: 50,
      size: Math.max(68, Math.min(96, 58 + label.length * 2)),
      eventIds: [],
      taskIds: [],
    };
  });

  const nodeIds = new Set(nodes.map((node) => node.id));
  const rawEdges = projection.graph?.edges ?? [];
  const edges = rawEdges
    .map((raw, index): LoopEdge | null => {
      const from = stringField(raw, "from");
      const to = stringField(raw, "to");
      if (!from || !to || !nodeIds.has(from) || !nodeIds.has(to)) return null;
      return {
        id: `${stringField(raw, "kind") || "stage"}:${from}->${to}:${index}`,
        from,
        to,
        kind: stringField(raw, "kind") || "stage",
        status: stringField(raw, "status") || "projection",
      };
    })
    .filter((edge): edge is LoopEdge => Boolean(edge));

  return { actions: [], edges, nodes, traceNodes: [] };
}

function stringField(raw: Record<string, unknown>, key: string): string {
  const value = raw[key];
  return value == null ? "" : String(value);
}

function stageKind(lens: string, id: string, label: string): LoopNodeKind {
  const text = `${lens} ${id} ${label}`.toLowerCase();
  if (text.includes("trace") || text.includes("plan") || text.includes("ingest") || text.includes("briefing") || text.includes("dev_done")) {
    return "trace";
  }
  if (text.includes("pattern") || text.includes("reactor") || text.includes("review") || text.includes("heartbeat")) return "behavior";
  if (text.includes("diagnosis") || text.includes("decision") || text.includes("work")) return "diagnosis";
  if (text.includes("test")) return "eval";
  if (text.includes("proposal") || text.includes("dispatch") || text.includes("act")) return "action";
  if (text.includes("verify") || text.includes("judge") || text.includes("gate")) return "verify";
  if (text.includes("learn") || text.includes("complete") || text.includes("done") || text.includes("improved") || text.includes("ship")) return "learn";
  return "behavior";
}

function compactLabel(value: string): string {
  if (value === "Verified Improvement") return "Verified";
  if (value === "Event Ingest") return "Ingest";
  if (value === "Worker Ack") return "Ack";
  if (value === "Rework/Ship") return "Ship";
  if (value === "Done/Rework") return "Done";
  if (value === "Complete/Emit") return "Complete";
  return value;
}

function humanize(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

export function measureGraphLabels(projection: MeasureLoopProjection | null): string[] {
  return buildMeasureGraphModel(projection)?.nodes.map((node) => node.displayLabel || node.label) ?? [];
}
