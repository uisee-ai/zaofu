import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getDeliveryTrace, getLoops, getMeasureLoops, postLoopAction, postLoopLearningPromotion } from "../../api/client";
import type {
  DeliveryRunTraceSpan,
  DeliveryTrace,
  LoopActionRecord,
  LoopDiagnosisRecord,
  LoopLearningRecord,
  LoopItem,
  LoopProjection,
  LoopVerificationRecord,
  MeasureLoopFeedItem,
  MeasureLoopMetric,
  MeasureLoopProjection,
  MeasureLoopStage,
} from "../../api/types";
import { layoutLoopModel, normalizeLoopLayoutMode } from "./BehaviorLoopLayout";
import type { LoopLayoutMode, ResolvedLoopLayoutMode } from "./BehaviorLoopLayout";
import { BehaviorLoopLayoutControl } from "./BehaviorLoopLayoutControl";
import { buildMeasureGraphModel } from "./BehaviorMeasureGraphModel";
import { buildLoopModel, filterLoopsForTarget, highlightSet, nodeLabel } from "./BehaviorLoopModel";
import type { LoopModel, LoopNode } from "./BehaviorLoopModel";
import { clockLabel, dtTone, formatDuration } from "./DeliveryTraceViewUtils";

type LoopStageName = "observe" | "diagnose" | "act" | "verify" | "learn";
type LoopLensId = "all" | "agent" | "verification" | "event_driven" | "hill_climbing";
type MeasureLineageKind = "metric" | "stage";

interface MeasureLineageRefs {
  source_event_ids?: string[];
  task_ids?: string[];
  trace_ids?: string[];
  loop_ids?: string[];
  graph_node_ids?: string[];
  source_projection_refs?: string[];
}

interface MeasureLineageSelection {
  detail?: string;
  id: string;
  kind: MeasureLineageKind;
  label: string;
  refs: MeasureLineageRefs;
}

const LOOP_LENS_OPTIONS: Array<{ id: LoopLensId; label: string }> = [
  { id: "all", label: "All" },
  { id: "agent", label: "Agent" },
  { id: "verification", label: "Verification" },
  { id: "event_driven", label: "Event-driven" },
  { id: "hill_climbing", label: "Hill-climbing" },
];

interface BehaviorLoopPageProps {
  projectId: string;
  featureIds: string[];
  onOpenTrace?: (traceId: string) => void;
  onSelectTask?: (taskId: string) => void;
}

export function BehaviorLoopPage({ featureIds, onOpenTrace, onSelectTask, projectId }: BehaviorLoopPageProps) {
  const deepLink = useMemo(() => readBehaviorLoopDeepLink(), []);
  const deepLinkAppliedRef = useRef(false);
  const [selectedTarget, setSelectedTarget] = useState(featureIds[0] ?? "");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [activeStage, setActiveStage] = useState<LoopStageName>(deepLink.stage || "observe");
  const [layoutMode, setLayoutMode] = useState<LoopLayoutMode>(deepLink.layout || "auto");
  const [loopLens, setLoopLens] = useState<LoopLensId>(deepLink.lens || "all");
  const [trace, setTrace] = useState<DeliveryTrace | null>(null);
  const [loopProjection, setLoopProjection] = useState<LoopProjection | null>(null);
  const [measureProjection, setMeasureProjection] = useState<MeasureLoopProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const [loopLoading, setLoopLoading] = useState(false);
  const [measureLoading, setMeasureLoading] = useState(false);
  const [error, setError] = useState("");
  const [loopError, setLoopError] = useState("");
  const [measureError, setMeasureError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionNotice, setActionNotice] = useState("");
  const [requestingActionId, setRequestingActionId] = useState("");
  const [requestingPromotionId, setRequestingPromotionId] = useState("");
  const [lineageSelection, setLineageSelection] = useState<MeasureLineageSelection | null>(null);

  useEffect(() => {
    if (featureIds.length && !featureIds.includes(selectedTarget)) {
      setSelectedTarget(featureIds[0]);
    }
  }, [featureIds, selectedTarget]);

  useEffect(() => {
    if (!selectedTarget) {
      setTrace(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    getDeliveryTrace(selectedTarget, projectId || undefined)
      .then((value) => {
        if (cancelled) return;
        setTrace(value);
        if (!deepLink.loopId && !deepLink.nodeId) setSelectedNodeId("");
      })
      .catch((err) => {
        if (!cancelled) setError(String(err?.message ?? err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [deepLink.loopId, deepLink.nodeId, projectId, selectedTarget]);

  const loadLoops = useCallback(async () => {
    let cancelled = false;
    setLoopLoading(true);
    setLoopError("");
    return getLoops(projectId || undefined)
      .then((value) => {
        if (cancelled) return;
        setLoopProjection(value);
      })
      .catch((err) => {
        if (!cancelled) setLoopError(String(err?.message ?? err));
      })
      .finally(() => {
        if (!cancelled) setLoopLoading(false);
      });
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    setLoopLoading(true);
    setLoopError("");
    getLoops(projectId || undefined)
      .then((value) => {
        if (cancelled) return;
        setLoopProjection(value);
      })
      .catch((err) => {
        if (!cancelled) setLoopError(String(err?.message ?? err));
      })
      .finally(() => {
        if (!cancelled) setLoopLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const loadMeasureLoops = useCallback(async () => {
    let cancelled = false;
    setMeasureLoading(true);
    setMeasureError("");
    return getMeasureLoops(projectId || undefined, selectedTarget || undefined, loopLens)
      .then((value) => {
        if (cancelled) return;
        setMeasureProjection(value);
      })
      .catch((err) => {
        if (!cancelled) setMeasureError(String(err?.message ?? err));
      })
      .finally(() => {
        if (!cancelled) setMeasureLoading(false);
      });
  }, [loopLens, projectId, selectedTarget]);

  useEffect(() => {
    setLineageSelection(null);
  }, [loopLens, selectedTarget]);

  useEffect(() => {
    let cancelled = false;
    setMeasureLoading(true);
    setMeasureError("");
    getMeasureLoops(projectId || undefined, selectedTarget || undefined, loopLens)
      .then((value) => {
        if (cancelled) return;
        setMeasureProjection(value);
      })
      .catch((err) => {
        if (!cancelled) setMeasureError(String(err?.message ?? err));
      })
      .finally(() => {
        if (!cancelled) setMeasureLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loopLens, projectId, selectedTarget]);

  const thick = trace?.thick_trace ?? null;
  const detailModel = useMemo(() => buildLoopModel(thick, loopProjection, selectedTarget, deepLink.loopId), [deepLink.loopId, loopProjection, selectedTarget, thick]);
  const measureGraphModel = useMemo(() => buildMeasureGraphModel(measureProjection), [measureProjection]);
  const graphSourceModel = measureGraphModel ?? detailModel;
  const effectiveLayoutMode = useMemo(() => {
    if (layoutMode !== "auto") return layoutMode;
    const hint = normalizeLoopLayoutMode(measureProjection?.graph?.layout_hint || "");
    return hint === "auto" ? "auto" : hint;
  }, [layoutMode, measureProjection]);
  const loopLayout = useMemo(() => layoutLoopModel(graphSourceModel, effectiveLayoutMode), [effectiveLayoutMode, graphSourceModel]);
  const model = loopLayout.model;
  const recentLoops = useMemo(
    () => filterLoopsForTarget(loopProjection?.loops ?? [], selectedTarget, thick?.related_loop_ids ?? []).slice(0, 8),
    [loopProjection, selectedTarget, thick],
  );
  const loopActions = loopProjection?.actions ?? [];
  const loopDiagnoses = loopProjection?.diagnoses ?? [];
  const loopVerifications = loopProjection?.verifications ?? [];
  const loopLearning = loopProjection?.learning ?? [];
  const lineageFeed = useMemo(
    () => filterMeasureFeed(measureProjection?.feed ?? [], lineageSelection?.refs),
    [lineageSelection, measureProjection],
  );
  const explicitSelectedNode = selectedNodeId ? model.nodes.find((node) => node.id === selectedNodeId) ?? null : null;
  const selectedNode = explicitSelectedNode ?? model.nodes[0] ?? null;
  const selectedStage = explicitSelectedNode ? stageForNode(explicitSelectedNode) : activeStage;
  const highlighted = useMemo(() => highlightSet(model, selectedNode?.id ?? ""), [model, selectedNode]);
  const selectNode = useCallback((nodeId: string) => {
    const node = model.nodes.find((item) => item.id === nodeId)
      ?? (nodeId.startsWith("trace:") ? model.nodes.find((item) => item.kind === "trace") : undefined);
    if (node) setActiveStage(stageForNode(node));
    setSelectedNodeId(node?.id ?? nodeId);
    writeBehaviorLoopDeepLink({
      lens: loopLens,
      layout: layoutMode,
      loopId: node?.loopId || deepLink.loopId,
      nodeId: node?.id ?? nodeId,
      stage: node ? stageForNode(node) : activeStage,
    });
  }, [activeStage, deepLink.loopId, layoutMode, loopLens, model.nodes]);
  const selectStage = useCallback((stage: LoopStageName) => {
    const node = firstNodeForStage(model, stage, deepLink.loopId);
    setActiveStage(stage);
    setSelectedNodeId(node?.id ?? "");
    writeBehaviorLoopDeepLink({
      lens: loopLens,
      layout: layoutMode,
      loopId: node?.loopId || deepLink.loopId,
      nodeId: node?.id,
      stage,
    });
  }, [deepLink.loopId, layoutMode, loopLens, model]);
  const selectMeasureStage = useCallback((stage: MeasureLoopStage) => {
    const mapped = loopStageForMeasureStage(stage);
    const node = model.nodes.find((item) => item.id === stage.id) ?? firstNodeForStage(model, mapped, deepLink.loopId);
    setActiveStage(mapped);
    setSelectedNodeId(node?.id ?? stage.id);
    setLineageSelection(measureLineageSelection("stage", stage));
    writeBehaviorLoopDeepLink({
      lens: loopLens,
      layout: layoutMode,
      loopId: node?.loopId || deepLink.loopId,
      nodeId: node?.id ?? stage.id,
      stage: mapped,
    });
  }, [deepLink.loopId, layoutMode, loopLens, model.nodes]);
  const selectMetric = useCallback((metric: MeasureLoopMetric) => {
    setLineageSelection(measureLineageSelection("metric", metric));
  }, []);
  const selectLayout = useCallback((mode: LoopLayoutMode) => {
    setLayoutMode(mode);
    writeBehaviorLoopDeepLink({
      lens: loopLens,
      layout: mode,
      loopId: selectedNode?.loopId || deepLink.loopId,
      nodeId: selectedNode?.id,
      stage: selectedStage,
    });
  }, [deepLink.loopId, loopLens, selectedNode, selectedStage]);
  const selectLens = useCallback((lens: LoopLensId) => {
    setLoopLens(lens);
    setSelectedNodeId("");
    setActiveStage("observe");
    writeBehaviorLoopDeepLink({
      lens,
      layout: layoutMode,
      stage: "observe",
    });
  }, [layoutMode]);

  useEffect(() => {
    if (!model.nodes.length || deepLinkAppliedRef.current) return;
    const node = resolveDeepLinkNode(model, deepLink)
      ?? (!deepLink.loopId && !deepLink.nodeId && !deepLink.stage ? model.nodes[0] ?? null : null);
    if (!node) return;
    deepLinkAppliedRef.current = true;
    setSelectedNodeId(node.id);
    setActiveStage(stageForNode(node));
  }, [deepLink, model]);
  const requestAction = useCallback(async (action: LoopNode) => {
    if (!action.loopId || !action.candidateId) {
      setActionError("action is missing loop or candidate id");
      return;
    }
    const requestId = `loop-action:${action.candidateId}:${randomSuffix()}`;
    setRequestingActionId(action.id);
    setActionError("");
    setActionNotice("");
    try {
      const result = await postLoopAction(action.loopId, {
        candidate_id: action.candidateId,
        suggested_action: action.suggestedAction,
        idempotency_key: requestId,
      }, projectId || undefined);
      setActionNotice(`${result.status}: ${result.mapped_event_type || result.terminal_event_type || result.action_id || "requested"}`);
      await loadLoops();
      await loadMeasureLoops();
    } catch (err) {
      setActionError(String(err instanceof Error ? err.message : err));
    } finally {
      setRequestingActionId("");
    }
  }, [loadLoops, loadMeasureLoops, projectId]);
  const requestPromotion = useCallback(async (item: LoopLearningRecord) => {
    if (!item.loop_id || !item.learning_id) {
      setActionError("learning artifact is missing loop or learning id");
      return;
    }
    const requestId = `loop-learning-promotion:${item.learning_id}:${randomSuffix()}`;
    setRequestingPromotionId(item.learning_id);
    setActionError("");
    setActionNotice("");
    try {
      const result = await postLoopLearningPromotion(item.loop_id, item.learning_id, {
        target: item.promotion_target || undefined,
        idempotency_key: requestId,
      }, projectId || undefined);
      setActionNotice(`${result.status}: ${result.proposal_ref || result.terminal_event_type || result.promotion_id || "promotion"}`);
      await loadLoops();
      await loadMeasureLoops();
    } catch (err) {
      setActionError(String(err instanceof Error ? err.message : err));
    } finally {
      setRequestingPromotionId("");
    }
  }, [loadLoops, loadMeasureLoops, projectId]);

  return (
    <div className="behavior-loop-page" data-testid="behavior-loop-page">
      <div className="behavior-loop-shell">
        <header className="behavior-loop-topbar">
          <div>
            <h2>Loop</h2>
            <span className="muted">measure-loop.v1 / multi-agent product delivery health</span>
          </div>
          <div className="behavior-loop-targets" aria-label="Delivery target selector">
            {featureIds.length ? featureIds.map((featureId) => (
              <button
                className={`behavior-loop-target ${featureId === selectedTarget ? "active" : ""}`}
                key={featureId}
                type="button"
                onClick={() => setSelectedTarget(featureId)}
              >
                {featureId}
              </button>
            )) : <span className="muted">No delivery target</span>}
          </div>
        </header>

        <LoopProjectionStrip
          activeLens={loopLens}
          activeLineage={lineageSelection}
          loading={loopLoading || measureLoading}
          measureProjection={measureProjection}
          onSelectLens={selectLens}
          onSelectMetric={selectMetric}
          projection={loopProjection}
          recentLoops={recentLoops}
        />

        <Sparkline spans={thick?.spans ?? []} />
        <LoopClosureRail
          activeStage={selectedStage}
          activeLineage={lineageSelection}
          measureProjection={measureProjection}
          onSelectMeasureStage={selectMeasureStage}
          onSelectStage={selectStage}
        />
        <MeasureLineagePanel
          feed={lineageFeed}
          onOpenTrace={onOpenTrace}
          onSelectTask={onSelectTask}
          selection={lineageSelection}
          totalFeedCount={measureProjection?.feed?.length ?? 0}
        />
        <BehaviorLoopLayoutControl
          mode={layoutMode}
          reason={loopLayout.reason}
          resolvedMode={loopLayout.resolvedMode}
          onSelect={selectLayout}
        />

        {loading ? <p className="muted">Loading loop…</p> : null}
        {error ? <p className="error" data-testid="behavior-loop-error">{error}</p> : null}
        {loopError ? <p className="error" data-testid="loop-error">{loopError}</p> : null}
        {measureError ? <p className="error" data-testid="measure-loop-error">{measureError}</p> : null}

        {thick || model.nodes.length ? (
          <main className="behavior-loop-workbench">
            <TraceFeed
              nodes={detailModel.traceNodes}
              selectedNodeId={selectedNode?.id ?? ""}
              spans={thick?.spans ?? []}
              onSelect={selectNode}
            />
            <LoopGraph
              highlighted={highlighted}
              layoutMode={layoutMode}
              model={model}
              resolvedLayout={loopLayout.resolvedMode}
              selectedNodeId={selectedNode?.id ?? ""}
              onSelect={selectNode}
            />
            <LoopInspector
              actionError={actionError}
              actionNotice={actionNotice}
              actionRows={loopActions}
              actions={detailModel.actions}
              diagnoses={loopDiagnoses}
              learning={loopLearning}
              node={selectedNode}
              requestingPromotionId={requestingPromotionId}
              requestingActionId={requestingActionId}
              verifications={loopVerifications}
              onRequestAction={requestAction}
              onRequestPromotion={requestPromotion}
            />
          </main>
        ) : (
          <section className="behavior-loop-empty">
            <h3>No loop trace yet</h3>
            <p className="muted">Select a delivery target after runtime writes delivery trace evidence.</p>
          </section>
        )}

      </div>
    </div>
  );
}

function LoopClosureRail({
  activeStage,
  activeLineage,
  measureProjection,
  onSelectMeasureStage,
  onSelectStage,
}: {
  activeStage: LoopStageName;
  activeLineage: MeasureLineageSelection | null;
  measureProjection: MeasureLoopProjection | null;
  onSelectMeasureStage: (stage: MeasureLoopStage) => void;
  onSelectStage: (stage: LoopStageName) => void;
}) {
  if (measureProjection?.stages?.length) {
    return (
      <section className="loop-closure-rail" aria-label="Loop delivery stages">
        {measureProjection.stages.map((stage) => {
          const mapped = loopStageForMeasureStage(stage);
          return (
            <LoopStage
              active={activeLineage?.kind === "stage" ? activeLineage.id === stage.id : activeStage === mapped}
              detail={stage.detail}
              key={stage.id}
              label={stage.label}
              measureStage={stage}
              stage={mapped}
              value={stage.value}
              tone={stage.tone || "info"}
              onSelect={onSelectStage}
              onSelectMeasure={onSelectMeasureStage}
            />
          );
        })}
      </section>
    );
  }
  return null;
}

function LoopStage({
  active,
  detail,
  label,
  measureStage,
  onSelect,
  onSelectMeasure,
  stage,
  tone,
  value,
}: {
  active: boolean;
  detail?: string;
  label: string;
  measureStage?: MeasureLoopStage;
  onSelect: (stage: LoopStageName) => void;
  onSelectMeasure?: (stage: MeasureLoopStage) => void;
  stage: LoopStageName;
  tone: string;
  value: number | string;
}) {
  return (
    <button
      className={`loop-closure-stage tone-${tone} ${active ? "active" : ""}`}
      data-testid={`loop-stage-${stage}`}
      type="button"
      onClick={() => measureStage && onSelectMeasure ? onSelectMeasure(measureStage) : onSelect(stage)}
      title={lineageTitle(measureStage)}
    >
      <strong>{label}</strong>
      <span>{value}</span>
      {detail ? <small>{detail}</small> : null}
    </button>
  );
}

function LoopProjectionStrip({
  activeLens,
  activeLineage,
  loading,
  measureProjection,
  onSelectLens,
  onSelectMetric,
  projection,
  recentLoops,
}: {
  activeLens: LoopLensId;
  activeLineage: MeasureLineageSelection | null;
  loading: boolean;
  measureProjection: MeasureLoopProjection | null;
  onSelectLens: (lens: LoopLensId) => void;
  onSelectMetric: (metric: MeasureLoopMetric) => void;
  projection: LoopProjection | null;
  recentLoops: LoopItem[];
}) {
  const summary = projection?.summary;
  const metrics = measureProjection?.metrics ?? [];
  const lensOptions = (measureProjection?.lenses?.length ? measureProjection.lenses : LOOP_LENS_OPTIONS)
    .map((lens) => ({ id: normalizeLoopLens(lens.id), label: lens.label }))
    .filter((lens, index, options) => options.findIndex((item) => item.id === lens.id) === index);
  return (
    <section className="loop-projection-strip" data-testid="loop-projection-summary">
      <div className="loop-projection-metrics">
        {metrics.length ? metrics.slice(0, 6).map((metric) => (
          <LoopMetric
            active={activeLineage?.kind === "metric" && activeLineage.id === metric.id}
            key={metric.id}
            metric={metric}
            onSelect={onSelectMetric}
          />
        )) : (
          <>
            <LoopMetric label="Total" value={summary?.total ?? 0} />
            <LoopMetric label="Open" value={summary?.open ?? 0} tone="warn" />
            <LoopMetric label="Recovered" value={summary?.recovered ?? 0} tone="ok" />
            <LoopMetric label="Exhausted" value={summary?.exhausted ?? 0} tone="err" />
            <LoopMetric label="Evals" value={summary?.eval_count ?? 0} />
            <LoopMetric label="Candidates" value={summary?.candidate_count ?? 0} />
          </>
        )}
      </div>
      {(() => {
        // design 101 §3 — eval-dataset spine (provisional proxy until S7).
        const ds = (measureProjection?.summary as {
          dataset?: {
            cases?: number;
            recovered?: number;
            pass_rate?: number | null;
            provisional?: boolean;
          };
        } | undefined)?.dataset;
        const pass =
          typeof ds?.pass_rate === "number" ? `${Math.round(ds.pass_rate * 100)}%` : "—";
        return (
          <div
            className="loop-dataset-spine"
            data-testid="loop-dataset-spine"
            style={{
              display: "flex",
              gap: 10,
              alignItems: "baseline",
              padding: "4px 8px",
              marginTop: 4,
              fontSize: 12,
              opacity: 0.85,
            }}
          >
            <span aria-hidden="true">🧬</span>
            <strong>dataset spine</strong>
            <span>{ds?.cases ?? 0} cases</span>
            <span>recovered {ds?.recovered ?? 0}</span>
            <span>pass {pass}</span>
            {ds?.provisional ? (
              <em style={{ opacity: 0.6 }} title="proxy from failure/rework events until S7 dataset store">
                provisional
              </em>
            ) : null}
          </div>
        );
      })()}
      <div className="loop-lens-strip" role="radiogroup" aria-label="Loop lens">
        {lensOptions.map((lens) => (
          <button
            aria-label={`${lens.label} loop lens`}
            aria-checked={activeLens === lens.id}
            className={`loop-lens-chip ${activeLens === lens.id ? "active" : ""}`}
            data-testid={`loop-lens-${lens.id}`}
            key={lens.id}
            role="radio"
            title={`${lens.label} loop lens`}
            type="button"
            onClick={() => onSelectLens(lens.id)}
          >
            <span className="loop-lens-icon" aria-hidden="true">{lensIcon(lens.id)}</span>
            <span className="loop-lens-label">{lens.label}</span>
          </button>
        ))}
      </div>
      <div className="loop-recent-list">
        {loading ? <span className="muted">Loading loops…</span> : null}
        {!loading && measureProjection ? (
          <span className="loop-recent-chip tone-info" title={measureProjection.source_projection_refs?.join(", ") || "measure-loop.v1"}>
            <strong>{measureProjection.active_lens}</strong>
            <span>{measureProjection.graph?.layout_hint || "projection"}</span>
          </span>
        ) : null}
        {!loading && recentLoops.length ? recentLoops.map((loop) => (
          <span className={`loop-recent-chip tone-${dtTone(loop.status)}`} key={loop.loop_id} title={loop.summary || loop.loop_id}>
            <strong>{loopKindLabel(loop.kind)}</strong>
            <span>{loop.status}</span>
          </span>
        )) : null}
        {!loading && !recentLoops.length ? <span className="muted">No loops projected</span> : null}
      </div>
    </section>
  );
}

function LoopMetric({
  active = false,
  label,
  metric,
  onSelect,
  tone = "info",
  value,
}: {
  active?: boolean;
  label?: string;
  metric?: MeasureLoopMetric;
  onSelect?: (metric: MeasureLoopMetric) => void;
  tone?: string;
  value?: number | string;
}) {
  const actualLabel = metric?.label ?? label ?? "";
  const actualValue = metric?.value ?? value ?? "";
  const actualTone = metric?.tone ?? tone;
  if (metric && onSelect) {
    return (
      <button
        className={`loop-metric tone-${actualTone} ${active ? "active" : ""}`}
        data-testid={`loop-metric-${metric.id}`}
        type="button"
        aria-label={`Inspect lineage for ${actualLabel}`}
        title={lineageTitle(metric)}
        onClick={() => onSelect(metric)}
      >
        <strong>{actualValue}</strong>
        <span>{actualLabel}</span>
      </button>
    );
  }
  return (
    <div className={`loop-metric tone-${actualTone}`}>
      <strong>{actualValue}</strong>
      <span>{actualLabel}</span>
    </div>
  );
}

function MeasureLineagePanel({
  feed,
  onOpenTrace,
  onSelectTask,
  selection,
  totalFeedCount,
}: {
  feed: MeasureLoopFeedItem[];
  onOpenTrace?: (traceId: string) => void;
  onSelectTask?: (taskId: string) => void;
  selection: MeasureLineageSelection | null;
  totalFeedCount: number;
}) {
  if (!selection && !totalFeedCount) return null;
  const refs = selection?.refs ?? {};
  const taskCount = refs.task_ids?.length ?? 0;
  const eventCount = refs.source_event_ids?.length ?? 0;
  const loopCount = refs.loop_ids?.length ?? 0;
  const projectionCount = refs.source_projection_refs?.length ?? 0;
  const title = selection ? `${selection.kind}: ${selection.label}` : "latest measure feed";
  return (
    <section className="loop-lineage-panel" data-testid="loop-lineage-panel" aria-label="Measure lineage evidence">
      <div className="loop-lineage-head">
        <div>
          <h3>{title}</h3>
          <span>{selection?.detail || `${totalFeedCount} projected events`}</span>
        </div>
        <div className="loop-lineage-counters">
          <span>{eventCount} events</span>
          <span>{taskCount} tasks</span>
          <span>{loopCount} loops</span>
          <span>{projectionCount} projections</span>
        </div>
      </div>
      <div className="loop-lineage-feed">
        {feed.slice(0, 10).map((item, index) => {
          const traceId = item.trace_id || "";
          const taskId = item.task_id || "";
          return (
            <div className={`loop-lineage-row tone-${dtTone(item.status || "")}`} key={`${item.event_id || item.seq || index}`}>
              <strong>{item.event_type || item.event_id || `event ${item.seq ?? index + 1}`}</strong>
              <span>{taskId || traceId || "project"}</span>
              <code>{item.event_id || traceId || item.seq || "-"}</code>
              {traceId || taskId ? (
                <span className="loop-lineage-actions">
                  {traceId && onOpenTrace ? (
                    <button type="button" onClick={() => onOpenTrace(traceId)}>
                      Trace
                    </button>
                  ) : null}
                  {taskId && onSelectTask ? (
                    <button type="button" onClick={() => onSelectTask(taskId)}>
                      Task
                    </button>
                  ) : null}
                </span>
              ) : null}
            </div>
          );
        })}
        {!feed.length ? <span className="muted">No matching feed rows.</span> : null}
      </div>
    </section>
  );
}

function measureLineageSelection(kind: MeasureLineageKind, item: MeasureLoopMetric | MeasureLoopStage): MeasureLineageSelection {
  return {
    detail: item.detail,
    id: item.id,
    kind,
    label: item.label,
    refs: measureRefs(item),
  };
}

function measureRefs(item?: MeasureLoopMetric | MeasureLoopStage | null): MeasureLineageRefs {
  if (!item) return {};
  return {
    graph_node_ids: item.graph_node_ids ?? [],
    loop_ids: item.loop_ids ?? [],
    source_event_ids: item.source_event_ids ?? [],
    source_projection_refs: item.source_projection_refs ?? [],
    task_ids: item.task_ids ?? [],
    trace_ids: item.trace_ids ?? [],
  };
}

function filterMeasureFeed(feed: MeasureLoopFeedItem[], refs?: MeasureLineageRefs): MeasureLoopFeedItem[] {
  if (!refs) return feed;
  const eventIds = new Set(refs.source_event_ids ?? []);
  const taskIds = new Set(refs.task_ids ?? []);
  const traceIds = new Set(refs.trace_ids ?? []);
  if (!eventIds.size && !taskIds.size && !traceIds.size) return feed;
  return feed.filter((item) =>
    (item.event_id && eventIds.has(item.event_id))
    || (item.task_id && taskIds.has(item.task_id))
    || (item.trace_id && traceIds.has(item.trace_id))
  );
}

function lineageTitle(item?: MeasureLoopMetric | MeasureLoopStage | null): string {
  const refs = measureRefs(item);
  const parts = [
    countLabel(refs.source_event_ids, "event"),
    countLabel(refs.task_ids, "task"),
    countLabel(refs.loop_ids, "loop"),
    countLabel(refs.source_projection_refs, "projection"),
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : item?.detail || "";
}

function countLabel(values: string[] | undefined, label: string): string {
  const count = values?.length ?? 0;
  return count ? `${count} ${label}${count === 1 ? "" : "s"}` : "";
}

function loopStageForMeasureStage(stage: MeasureLoopStage): LoopStageName {
  const id = stage.id.toLowerCase();
  if (id.includes("verify") || id.includes("review") || id.includes("test") || id.includes("judge")) return "verify";
  if (id.includes("dispatch") || id.includes("briefing") || id.includes("ingest") || id.includes("reactor") || id.includes("decision")) return "act";
  if (id.includes("work") || id.includes("heartbeat") || id.includes("pattern") || id.includes("diagnosis")) return "diagnose";
  if (id.includes("rework") || id.includes("ship") || id.includes("proposal") || id.includes("improved") || id.includes("complete")) return "learn";
  return "observe";
}

function lensIcon(lens: LoopLensId): string {
  if (lens === "agent") return "A";
  if (lens === "verification") return "V";
  if (lens === "event_driven") return "E";
  if (lens === "hill_climbing") return "H";
  return "All";
}

function loopKindLabel(kind: string): string {
  if (kind === "stuck_worker") return "Worker health";
  if (kind === "missing_evidence") return "Evidence";
  if (kind === "source_coverage_gap") return "Coverage";
  return kind.replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function Sparkline({ spans }: { spans: DeliveryRunTraceSpan[] }) {
  const bars = spans.slice(0, 54).map((span, index) => {
    const duration = Math.max(1, Math.min(100, Math.round((span.duration_ms ?? 1200) / 120)));
    const tone = span.status === "failed" ? "err" : span.status === "running" ? "info" : span.degraded ? "warn" : "ok";
    return { height: 18 + (duration % 28), key: `${span.span_id}-${index}`, tone };
  });
  return (
    <div className="behavior-loop-sparkline" aria-label="Trace activity sparkline">
      {bars.length ? bars.map((bar) => (
        <span className={`behavior-loop-bar tone-${bar.tone}`} key={bar.key} style={{ height: `${bar.height}px` }} />
      )) : <span className="muted">No spans</span>}
    </div>
  );
}

function TraceFeed({
  nodes,
  onSelect,
  selectedNodeId,
  spans,
}: {
  nodes: LoopNode[];
  onSelect: (nodeId: string) => void;
  selectedNodeId: string;
  spans: DeliveryRunTraceSpan[];
}) {
  const spansByNode = new Map(nodes.map((node, index) => [node.id, spans[index]]));
  return (
    <aside className="behavior-loop-feed" data-testid="behavior-loop-feed">
      <div className="behavior-loop-panel-head">
        <h3>Traces</h3>
        <span className="live-pill">LIVE</span>
      </div>
      <div className="behavior-loop-feed-list">
        {nodes.map((node) => {
          const span = spansByNode.get(node.id);
          const status = span?.status || node.status;
          return (
            <button
              className={`behavior-loop-feed-row ${selectedNodeId === node.id ? "active" : ""}`}
              key={node.id}
              type="button"
              onClick={() => onSelect(node.id)}
            >
              <span className="behavior-loop-feed-meta">
                <span>{clockLabel(span?.started_at)}</span>
                <strong>{node.label}</strong>
                <span>{formatCurrency(span?.cost_usd)}</span>
              </span>
              <span className="behavior-loop-feed-summary">{node.summary}</span>
              <span className={`badge badge-${dtTone(status)}`}>{status || "trace"}</span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

function LoopGraph({
  highlighted,
  layoutMode,
  model,
  onSelect,
  resolvedLayout,
  selectedNodeId,
}: {
  highlighted: Set<string>;
  layoutMode: LoopLayoutMode;
  model: LoopModel;
  resolvedLayout: ResolvedLoopLayoutMode;
  onSelect: (nodeId: string) => void;
  selectedNodeId: string;
}) {
  return (
    <section className="behavior-loop-graph" data-testid="behavior-loop-graph">
      <div className="behavior-loop-panel-head">
        <h3>Graph</h3>
        <span className="live-pill">{layoutMode === "auto" ? `AUTO/${resolvedLayout.toUpperCase()}` : resolvedLayout.toUpperCase()}</span>
      </div>
      <div className="behavior-loop-canvas">
        <svg className="behavior-loop-edges" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          {model.edges.map((edge) => {
            const source = model.nodes.find((node) => node.id === edge.from);
            const target = model.nodes.find((node) => node.id === edge.to);
            if (!source || !target) return null;
            const active = highlighted.has(edge.id);
            return (
              <line
                className={`behavior-loop-edge kind-${edge.kind} ${active ? "active" : ""}`}
                key={edge.id}
                x1={source.x}
                x2={target.x}
                y1={source.y}
                y2={target.y}
              />
            );
          })}
        </svg>
        {model.nodes.map((node) => {
          const active = selectedNodeId === node.id || highlighted.has(node.id);
          return (
            <button
              className={`behavior-loop-node kind-${node.kind} tone-${dtTone(node.status)} ${active ? "active" : ""}`}
              data-testid="behavior-loop-node"
              key={node.id}
              style={{
                height: `${node.size}px`,
                left: `${node.x}%`,
                top: `${node.y}%`,
                width: `${node.size}px`,
              }}
              title={`${node.kind}: ${node.label}`}
              type="button"
              onClick={() => onSelect(node.id)}
            >
              <strong>{nodeLabel(node)}</strong>
              <span>{node.summary}</span>
            </button>
          );
        })}
        <div className="behavior-loop-legend">
          <span><i className="legend-user" />USER</span>
          <span><i className="legend-ai" />AI</span>
          <span><i className="legend-tool" />TOOL</span>
          <span><i className="legend-loop" />LOOP</span>
        </div>
      </div>
    </section>
  );
}

function LoopInspector({
  actionError,
  actionNotice,
  actionRows,
  actions,
  diagnoses,
  learning,
  node,
  onRequestAction,
  onRequestPromotion,
  requestingActionId,
  requestingPromotionId,
  verifications,
}: {
  actionError: string;
  actionNotice: string;
  actionRows: LoopActionRecord[];
  actions: LoopNode[];
  diagnoses: LoopDiagnosisRecord[];
  learning: LoopLearningRecord[];
  node: LoopNode | null;
  onRequestAction: (action: LoopNode) => void;
  onRequestPromotion: (item: LoopLearningRecord) => void;
  requestingActionId: string;
  requestingPromotionId: string;
  verifications: LoopVerificationRecord[];
}) {
  const relatedActions = actionRows.filter((action) => (
    (node?.candidateId && action.candidate_id === node.candidateId)
    || (node?.loopId && action.loop_id === node.loopId)
  ));
  const relatedDiagnoses = diagnoses.filter((diagnosis) => (
    (node?.candidateId && diagnosis.candidate_id === node.candidateId)
    || (node?.loopId && diagnosis.loop_id === node.loopId)
  ));
  const relatedVerifications = verifications.filter((verification) => (
    (node?.verificationId && verification.verification_id === node.verificationId)
    || (node?.candidateId && verification.candidate_id === node.candidateId)
    || (node?.loopId && verification.loop_id === node.loopId)
  ));
  const relatedLearning = learning.filter((item) => (
    (node?.learningId && item.learning_id === node.learningId)
    || (node?.verificationId && item.verification_id === node.verificationId)
    || (node?.candidateId && item.candidate_id === node.candidateId)
    || (node?.loopId && item.loop_id === node.loopId)
  ));
  return (
    <aside className="behavior-loop-inspector" data-testid="behavior-loop-inspector">
      <div className="behavior-loop-panel-head">
        <h3>Inspector</h3>
        <span className="muted">{node?.kind ?? "node"}</span>
      </div>
      {node ? (
        <>
          <div className="behavior-loop-inspector-card">
            <span className={`badge badge-${dtTone(node.status)}`}>{node.status || node.kind}</span>
            <h4>{node.label}</h4>
            <p>{node.summary}</p>
            <dl>
              <div><dt>Kind</dt><dd>{node.kind}</dd></div>
              {node.loopId ? <div><dt>Loop</dt><dd>{node.loopId}</dd></div> : null}
              {node.candidateId ? <div><dt>Candidate</dt><dd>{node.candidateId}</dd></div> : null}
              <div><dt>Task refs</dt><dd>{node.taskIds.join(", ") || "-"}</dd></div>
              <div><dt>Event refs</dt><dd>{node.eventIds.join(", ") || "-"}</dd></div>
              {node.fixLayer ? <div><dt>Fix layer</dt><dd>{node.fixLayer}</dd></div> : null}
              {node.fingerprint ? <div><dt>Fingerprint</dt><dd>{node.fingerprint}</dd></div> : null}
              {node.suggestedAction ? <div><dt>Action</dt><dd>{node.suggestedAction}</dd></div> : null}
              {node.latestActionStatus ? <div><dt>Status</dt><dd>{node.latestActionStatus}</dd></div> : null}
              {node.latestVerificationStatus ? <div><dt>Verify</dt><dd>{node.latestVerificationStatus}</dd></div> : null}
              {node.artifactKind ? <div><dt>Artifact</dt><dd>{node.artifactKind}</dd></div> : null}
            </dl>
          </div>
          <DiagnosisPanel diagnoses={relatedDiagnoses} />
          <div className="behavior-loop-actions">
            <h4>Suggested actions</h4>
            {actions.length ? actions.map((action) => (
                <button
                  className="behavior-loop-action"
                  disabled={!canRequestAction(action) || requestingActionId === action.id}
                  key={action.id}
                  type="button"
                  onClick={() => onRequestAction(action)}
                >
                  <strong>{action.label}</strong>
                  <span>{requestingActionId === action.id ? "requesting..." : action.latestActionStatus || action.summary}</span>
                </button>
              )) : <p className="muted">No suggested action</p>}
            {actionNotice ? <p className="behavior-loop-action-notice">{actionNotice}</p> : null}
            {actionError ? <p className="error">{actionError}</p> : null}
            <ActionTimeline actions={relatedActions} />
          </div>
          <VerificationPanel verifications={relatedVerifications} />
          <LearningPanel
            learning={relatedLearning}
            requestingPromotionId={requestingPromotionId}
            onRequestPromotion={onRequestPromotion}
          />
        </>
      ) : (
        <p className="muted">Select a trace row or graph node.</p>
      )}
    </aside>
  );
}

function DiagnosisPanel({ diagnoses }: { diagnoses: LoopDiagnosisRecord[] }) {
  if (!diagnoses.length) return null;
  return (
    <div className="behavior-loop-actions">
      <h4>Diagnose</h4>
      {diagnoses.slice(0, 3).map((diagnosis) => (
        <div className="behavior-loop-action-step" key={diagnosis.diagnosis_id}>
          <span className="badge badge-warn">{diagnosis.fix_layer || "unknown"}</span>
          <strong>{diagnosis.recommended_action || diagnosis.source_kind || "diagnosis"}</strong>
          <code>{(diagnosis.evidence_refs ?? []).join(" -> ") || diagnosis.diagnosis_id}</code>
          <small>{diagnosis.reason || `${Math.round((diagnosis.confidence ?? 0) * 100)}% confidence`}</small>
        </div>
      ))}
    </div>
  );
}

function ActionTimeline({ actions }: { actions: LoopActionRecord[] }) {
  if (!actions.length) return <p className="muted">No action requested yet</p>;
  return (
    <div className="behavior-loop-action-timeline">
      {actions.slice(0, 5).map((action) => (
        <div className="behavior-loop-action-step" key={action.action_id}>
          <span className={`badge badge-${dtTone(action.status)}`}>{action.status}</span>
          <strong>{action.suggested_action || action.mapped_action || action.action_id}</strong>
          <code>{[
            action.request_event_id,
            action.mapped_event_id,
            action.terminal_event_id,
          ].filter(Boolean).join(" -> ") || action.action_id}</code>
          {action.reason || action.outcome ? <small>{action.outcome || action.reason}</small> : null}
        </div>
      ))}
    </div>
  );
}

function VerificationPanel({ verifications }: { verifications: LoopVerificationRecord[] }) {
  return (
    <div className="behavior-loop-actions">
      <h4>Verify</h4>
      {verifications.length ? verifications.slice(0, 5).map((verification) => (
        <div className="behavior-loop-action-step" key={verification.verification_id}>
          <span className={`badge badge-${dtTone(verification.result || verification.status)}`}>
            {verification.result || verification.status}
          </span>
          <strong>{verification.terminal_event_type || verification.mode || "verification"}</strong>
          <code>{[
            verification.request_event_id,
            verification.terminal_event_id,
            verification.completed_event_id,
          ].filter(Boolean).join(" -> ") || verification.verification_id}</code>
          {verification.reason ? <small>{verification.reason}</small> : null}
          {verification.missing_evidence?.length ? (
            <small>missing: {verification.missing_evidence.join(", ")}</small>
          ) : null}
          {verification.next_check ? <small>next: {verification.next_check}</small> : null}
        </div>
      )) : <p className="muted">No verification yet</p>}
    </div>
  );
}

function LearningPanel({
  learning,
  onRequestPromotion,
  requestingPromotionId,
}: {
  learning: LoopLearningRecord[];
  onRequestPromotion: (item: LoopLearningRecord) => void;
  requestingPromotionId: string;
}) {
  return (
    <div className="behavior-loop-actions">
      <h4>Learn</h4>
      {learning.length ? learning.slice(0, 5).map((item) => (
        <div className="behavior-loop-action-step" key={item.learning_id}>
          <span className={`badge badge-${dtTone(item.status || "candidate")}`}>{item.artifact_kind || "artifact"}</span>
          <strong>{item.promotion_path || item.status || "learning"}</strong>
          <code>{item.promotion_ref || item.artifact_ref || item.learning_id}</code>
          <small>{promotionLabel(item)}</small>
          <button
            className="behavior-loop-action"
            disabled={!canRequestPromotion(item) || requestingPromotionId === item.learning_id}
            type="button"
            onClick={() => onRequestPromotion(item)}
          >
            <strong>{requestingPromotionId === item.learning_id ? "Promoting..." : "Promote"}</strong>
            <span>{item.promotion_target || "backlog_candidate"}</span>
          </button>
          {item.summary ? <small>{item.summary}</small> : null}
        </div>
      )) : <p className="muted">No learning artifact yet</p>}
    </div>
  );
}

function canRequestAction(action: LoopNode): boolean {
  if (!action.loopId || !action.candidateId) return false;
  return !["pending", "mapped", "running", "applied", "completed"].includes(action.latestActionStatus || "");
}

function canRequestPromotion(item: LoopLearningRecord): boolean {
  if (!item.loop_id || !item.learning_id) return false;
  return !["requested", "materialized"].includes(item.promotion_status || "");
}

function promotionLabel(item: LoopLearningRecord): string {
  const status = item.promotion_status || "not_requested";
  const target = item.promotion_target || "backlog_candidate";
  if (item.promotion_ref) return `${status} -> ${target} (${item.promotion_ref})`;
  if (item.promotion_reason) return `${status} -> ${target}: ${item.promotion_reason}`;
  return `${status} -> ${target}`;
}

function readBehaviorLoopDeepLink(): { layout: LoopLayoutMode; lens: LoopLensId; loopId: string; nodeId: string; stage: LoopStageName | "" } {
  const params = new URLSearchParams(window.location.search);
  const stage = normalizeStage(params.get("stage") || "");
  return {
    layout: normalizeLoopLayoutMode(params.get("layout") || ""),
    lens: normalizeLoopLens(params.get("lens") || ""),
    loopId: params.get("loop_id") || "",
    nodeId: params.get("node_id") || "",
    stage,
  };
}

function writeBehaviorLoopDeepLink({
  lens,
  layout,
  loopId,
  nodeId,
  stage,
}: {
  lens?: LoopLensId;
  layout?: LoopLayoutMode;
  loopId?: string;
  nodeId?: string;
  stage: LoopStageName;
}) {
  const params = new URLSearchParams(window.location.search);
  params.set("page", "behavior-loop");
  params.set("stage", stage);
  if (lens && lens !== "all") params.set("lens", lens);
  else params.delete("lens");
  if (layout && layout !== "auto") params.set("layout", layout);
  else params.delete("layout");
  if (loopId) params.set("loop_id", loopId);
  else params.delete("loop_id");
  if (nodeId) params.set("node_id", nodeId);
  else params.delete("node_id");
  window.history.replaceState(null, "", `?${params.toString()}`);
}

function normalizeStage(value: string): LoopStageName | "" {
  return value === "observe" || value === "diagnose" || value === "act" || value === "verify" || value === "learn"
    ? value
    : "";
}

function normalizeLoopLens(value: string): LoopLensId {
  return LOOP_LENS_OPTIONS.some((item) => item.id === value) ? value as LoopLensId : "all";
}

function resolveDeepLinkNode(model: LoopModel, link: { loopId: string; nodeId: string; stage: LoopStageName | "" }): LoopNode | null {
  if (link.nodeId) {
    const direct = model.nodes.find((node) => node.id === link.nodeId);
    if (direct) return direct;
  }
  if (link.stage) return firstNodeForStage(model, link.stage, link.loopId);
  if (link.loopId) {
    return model.nodes.find((node) => node.loopId === link.loopId) ?? null;
  }
  return null;
}

function firstNodeForStage(model: LoopModel, stage: LoopStageName, loopId = ""): LoopNode | null {
  const nodes = model.nodes.filter((node) => stageForNode(node) === stage);
  const scoped = loopId ? nodes.find((node) => node.loopId === loopId) : null;
  return scoped ?? nodes[0] ?? null;
}

function stageForNode(node: LoopNode): LoopStageName {
  if (node.kind === "trace") return "observe";
  if (node.kind === "action") return "act";
  if (node.kind === "verify") return "verify";
  if (node.kind === "learn") return "learn";
  return "diagnose";
}

function randomSuffix(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatCurrency(value?: number): string {
  if (value == null) return "$0.00";
  return `$${value.toFixed(2)}`;
}
