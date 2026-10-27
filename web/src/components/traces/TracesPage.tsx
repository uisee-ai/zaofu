import { Search } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

import { getProjectTraces, getTraceDetail, getTraceEventRaw, getTraceSpans } from "../../api/client";
import type { RecentEvent, TraceDetail, TraceEventDetail, TraceSpanPage, TraceSummary } from "../../api/types";
import { useTraceRefresh } from "./useTraceRefresh";
import type { LiveState } from "../../app/sharedTypes";
import type { TraceViewerActions, TraceViewerResources } from "./TraceViewer";
import type { TraceInspectorTab } from "./TraceInspector";
import type { TraceSpanView } from "./TraceSpanViews";
import {
  errorMessage,
  formatDuration,
  formatTimestamp,
  mergeTraceEvents,
  mergeTraceRows,
  optionValues,
  persistTraceFilters,
  readInitialTraceFilters,
  readInitialSpanId,
  readInitialTraceId,
  traceDurationSeconds,
  traceMatchesFilters,
  traceStatus,
  uniqueValues,
  writeTraceSelection,
  type TraceDurationFilter,
  type TraceFilters,
  type TraceStatusFilter,
} from "./traceModel";
import {
  resolveTraceViewerMode,
  type ResolvedTraceViewerMode,
  type TraceViewerMode,
} from "./traceSpanModel";

const TRACE_LIST_LIMIT = 50;
const TRACE_DETAIL_LIMIT = 80;
const TRACE_SPAN_LIMIT = 100;
const LazyTraceViewer = lazy(() => import("./TraceViewer").then((module) => ({
  default: module.TraceViewer,
})));

export function TracesPage({
  recentEvents = [],
  liveState,
  projectId,
}: {
  recentEvents?: RecentEvent[];
  liveState: LiveState;
  projectId: string;
}) {
  const [filters, setFilters] = useState<TraceFilters>(readInitialTraceFilters);
  const [rows, setRows] = useState<TraceSummary[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selectedTraceId, setSelectedTraceId] = useState(readInitialTraceId);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(() => Boolean(readInitialTraceId()));
  const [detailError, setDetailError] = useState("");
  const [earlierLoading, setEarlierLoading] = useState(false);
  const [earlierError, setEarlierError] = useState("");
  const [spanPage, setSpanPage] = useState<TraceSpanPage | null>(null);
  const [spanLoading, setSpanLoading] = useState(() => Boolean(readInitialTraceId()));
  const [spanError, setSpanError] = useState("");
  const [earlierSpansLoading, setEarlierSpansLoading] = useState(false);
  const [earlierSpansError, setEarlierSpansError] = useState("");
  const [viewerMode, setViewerMode] = useState<TraceViewerMode>(() => readInitialSpanId() ? "spans" : "auto");
  const [spanView, setSpanView] = useState<TraceSpanView>("tree");
  const [selectedSpanId, setSelectedSpanId] = useState(readInitialSpanId);
  const [selectedStageId, setSelectedStageId] = useState("");
  const [selectedEventId, setSelectedEventId] = useState("");
  const [inspectorTab, setInspectorTab] = useState<TraceInspectorTab>("overview");
  const [rawDetail, setRawDetail] = useState<TraceEventDetail | null>(null);
  const [rawLoading, setRawLoading] = useState(false);
  const [rawError, setRawError] = useState("");
  const [sourceSeq, setSourceSeq] = useState(0);
  const revision = useTraceRefresh(projectId, recentEvents, liveState,
    listLoading || detailLoading || spanLoading || loadingMore || earlierLoading || earlierSpansLoading, sourceSeq);
  const listScopeRef = useRef(projectId);
  const detailScopeRef = useRef(`${projectId}::${selectedTraceId}`);
  listScopeRef.current = `${projectId}::${revision}`;
  detailScopeRef.current = `${projectId}::${selectedTraceId}::${revision}`;
  const loadedProject = useRef("");
  const loadedDetail = useRef("");
  const loadedSpans = useRef("");

  useEffect(() => {
    let cancelled = false;
    if (loadedProject.current !== projectId) { setRows([]); setSourceSeq(0); }
    loadedProject.current = projectId;
    setListLoading(true);
    setListError("");
    setHasMore(false);
    setNextCursor(null);
    setLoadingMore(false);
    if (!projectId) return undefined;
    void getProjectTraces(projectId, { limit: TRACE_LIST_LIMIT })
      .then((page) => {
        if (cancelled) return;
        setRows(page.items.slice(0, TRACE_LIST_LIMIT));
        setSourceSeq(page.as_of_seq);
        setHasMore(page.has_more);
        setNextCursor(page.next_cursor);
      })
      .catch((error: unknown) => {
        if (!cancelled) setListError(errorMessage(error));
      })
      .finally(() => {
        if (!cancelled) setListLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, revision]);

  useEffect(() => {
    if (!selectedTraceId || !projectId) {
      setDetail(null);
      setDetailError("");
      setDetailLoading(Boolean(selectedTraceId));
      return undefined;
    }
    let cancelled = false;
    const scope = `${projectId}::${selectedTraceId}`;
    if (loadedDetail.current !== scope) setDetail(null);
    loadedDetail.current = scope;
    setDetailLoading(true);
    setDetailError("");
    setEarlierLoading(false);
    setEarlierError("");
    void getTraceDetail(selectedTraceId, projectId, { limit: TRACE_DETAIL_LIMIT })
      .then((value) => {
        if (!cancelled) setDetail(value);
      })
      .catch((error: unknown) => {
        if (!cancelled) setDetailError(errorMessage(error));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, selectedTraceId, revision]);

  useEffect(() => {
    if (!selectedTraceId || !projectId) {
      setSpanPage(null);
      setSpanError("");
      setSpanLoading(Boolean(selectedTraceId));
      setEarlierSpansLoading(false);
      setEarlierSpansError("");
      return undefined;
    }
    let cancelled = false;
    const scope = `${projectId}::${selectedTraceId}`;
    if (loadedSpans.current !== scope) setSpanPage(null);
    loadedSpans.current = scope;
    setSpanLoading(true);
    setSpanError("");
    setEarlierSpansLoading(false);
    setEarlierSpansError("");
    const focusedSpanId = selectedSpanId;
    void getTraceSpans(selectedTraceId, projectId, {
      limit: TRACE_SPAN_LIMIT,
      spanId: focusedSpanId || undefined,
    })
      .then((value) => {
        if (cancelled) return;
        const focused = value.focused_item;
        setSpanPage(focused && !value.items.some((span) => span.span_id === focused.span_id)
          ? { ...value, items: [...value.items, focused] }
          : value);
      })
      .catch((error: unknown) => {
        if (!cancelled) setSpanError(errorMessage(error));
      })
      .finally(() => {
        if (!cancelled) setSpanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, selectedTraceId, revision]);

  const resolvedViewerMode = resolveTraceViewerMode(viewerMode, spanLoading, detailLoading, spanPage, detail);
  useEffect(() => {
    if (resolvedViewerMode !== "spans" || !spanPage?.items.length || !selectedTraceId) return;
    if (spanPage.items.some((span) => span.span_id === selectedSpanId)) return;
    const firstSpanId = spanPage.items[0].span_id;
    setSelectedSpanId(firstSpanId);
    writeTraceSelection(selectedTraceId, firstSpanId);
  }, [resolvedViewerMode, selectedSpanId, selectedTraceId, spanPage]);

  const selectedSpan = useMemo(
    () => spanPage?.items.find((span) => span.span_id === selectedSpanId) ?? null,
    [selectedSpanId, spanPage?.items],
  );

  const selectedEvent = useMemo(
    () => detail?.timeline.find((event) => event.id === selectedEventId) ?? null,
    [detail?.timeline, selectedEventId],
  );
  const selectedEventHasRaw = Boolean(selectedEvent?.id && selectedEvent.has_raw !== false);
  const rawEventId = resolvedViewerMode === "spans"
    ? selectedSpan?.source_event_ids[0] ?? ""
    : resolvedViewerMode === "events" && selectedEventHasRaw
      ? selectedEventId
      : "";

  useEffect(() => {
    if (!projectId || inspectorTab !== "raw" || !rawEventId) {
      setRawDetail(null);
      setRawError("");
      setRawLoading(false);
      return undefined;
    }
    let cancelled = false;
    setRawDetail(null);
    setRawLoading(true);
    setRawError("");
    void getTraceEventRaw(rawEventId, projectId)
      .then((value) => {
        if (!cancelled) setRawDetail(value);
      })
      .catch((error: unknown) => {
        if (!cancelled) setRawError(errorMessage(error));
      })
      .finally(() => {
        if (!cancelled) setRawLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [inspectorTab, projectId, rawEventId]);

  useEffect(() => {
    persistTraceFilters(filters);
  }, [filters]);

  useEffect(() => {
    if (!selectedTraceId) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setSelectedTraceId("");
      setSelectedSpanId("");
      setSelectedStageId("");
      setSelectedEventId("");
      setInspectorTab("overview");
      writeTraceSelection("", "");
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selectedTraceId]);

  const roles = useMemo(() => uniqueValues(rows.flatMap((row) => row.actors ?? [])), [rows]);
  const backends = useMemo(() => uniqueValues(rows.flatMap((row) => row.backends ?? [])), [rows]);
  const visibleRows = useMemo(
    () => rows.filter((row) => traceMatchesFilters(row, filters)),
    [filters, rows],
  );

  const openTrace = (traceId: string) => {
    setSelectedTraceId(traceId);
    setDetail(null);
    setDetailLoading(true);
    setSpanPage(null);
    setSpanLoading(true);
    setViewerMode("auto");
    setSpanView("tree");
    setSelectedSpanId("");
    setSelectedStageId("");
    setSelectedEventId("");
    setInspectorTab("overview");
    writeTraceSelection(traceId, "");
  };

  const closeTrace = () => {
    setSelectedTraceId("");
    setSelectedSpanId("");
    setSelectedStageId("");
    setSelectedEventId("");
    setInspectorTab("overview");
    writeTraceSelection("", "");
  };

  const selectViewerMode = (mode: ResolvedTraceViewerMode) => {
    setViewerMode(mode);
    setInspectorTab("overview");
    writeTraceSelection(selectedTraceId, mode === "spans" ? selectedSpanId : "");
  };

  const selectSpan = (spanId: string) => {
    setSelectedSpanId(spanId);
    setSelectedStageId("");
    setSelectedEventId("");
    setInspectorTab("overview");
    writeTraceSelection(selectedTraceId, spanId);
  };

  const selectStage = (stageId: string) => {
    setSelectedStageId(stageId);
    setSelectedEventId("");
    setInspectorTab("overview");
    writeTraceSelection(selectedTraceId, "");
  };

  const selectEvent = (eventId: string) => {
    setSelectedEventId(eventId);
    setSelectedStageId("");
    setInspectorTab("overview");
    writeTraceSelection(selectedTraceId, "");
  };

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    const requestScope = `${projectId}::${revision}`;
    setLoadingMore(true);
    setListError("");
    try {
      const page = await getProjectTraces(projectId || undefined, {
        cursor: nextCursor,
        limit: TRACE_LIST_LIMIT,
      });
      if (listScopeRef.current !== requestScope) return;
      setRows((current) => mergeTraceRows(current, page.items));
      setHasMore(page.has_more);
      setNextCursor(page.next_cursor);
    } catch (error: unknown) {
      if (listScopeRef.current === requestScope) setListError(errorMessage(error));
    } finally {
      if (listScopeRef.current === requestScope) setLoadingMore(false);
    }
  };

  const loadEarlierEvents = async () => {
    if (!selectedTraceId || !detail?.next_cursor || earlierLoading || detailLoading) return;
    const traceId = selectedTraceId;
    const requestScope = `${projectId}::${traceId}::${revision}`;
    setEarlierLoading(true);
    setEarlierError("");
    try {
      const page = await getTraceDetail(traceId, projectId || undefined, {
        cursor: detail.next_cursor,
        limit: TRACE_DETAIL_LIMIT,
      });
      if (detailScopeRef.current !== requestScope) return;
      setDetail((current) => {
        if (current?.trace_id !== page.trace_id) return current;
        const timeline = mergeTraceEvents(page.timeline, current.timeline);
        return {
          ...current,
          first_seq: page.first_seq,
          first_ts: page.first_ts,
          has_more: page.has_more,
          next_cursor: page.next_cursor,
          timeline,
          truncated: timeline.length < current.event_count,
        };
      });
    } catch (error: unknown) {
      if (detailScopeRef.current === requestScope) setEarlierError(errorMessage(error));
    } finally {
      if (detailScopeRef.current === requestScope) setEarlierLoading(false);
    }
  };

  const loadEarlierSpans = async () => {
    if (!selectedTraceId || !spanPage?.next_cursor || earlierSpansLoading || spanLoading) return;
    const traceId = selectedTraceId;
    const requestScope = `${projectId}::${traceId}::${revision}`;
    setEarlierSpansLoading(true);
    setEarlierSpansError("");
    try {
      const page = await getTraceSpans(traceId, projectId || undefined, {
        cursor: spanPage.next_cursor,
        limit: TRACE_SPAN_LIMIT,
      });
      if (detailScopeRef.current !== requestScope) return;
      setSpanPage((current) => current?.trace_id === page.trace_id ? {
        ...current,
        has_more: page.has_more,
        items: mergeSpanRows(page.items, current.items),
        next_cursor: page.next_cursor,
      } : current);
    } catch (error: unknown) {
      if (detailScopeRef.current === requestScope) setEarlierSpansError(errorMessage(error));
    } finally {
      if (detailScopeRef.current === requestScope) setEarlierSpansLoading(false);
    }
  };

  const viewerResources: TraceViewerResources = {
    detail: { data: detail, error: detailError, loading: detailLoading },
    spans: { data: spanPage, error: spanError, loading: spanLoading },
    raw: { data: rawDetail, error: rawError, loading: rawLoading },
  };
  const viewerActions: TraceViewerActions = {
    close: closeTrace,
    loadEarlierEvents: () => void loadEarlierEvents(),
    loadEarlierSpans: () => void loadEarlierSpans(),
    selectEvent,
    selectInspectorTab: setInspectorTab,
    selectMode: selectViewerMode,
    selectSpan,
    selectSpanView: setSpanView,
    selectStage,
  };

  return (
    <div className="traces-page" data-testid="traces-page">
      <div
        aria-hidden={selectedTraceId ? true : undefined}
        className={`trace-index-context${selectedTraceId ? " trace-index-context-hidden" : ""}`}
      >
        <header className="traces-heading">
          <div>
            <h2>Traces</h2>
            <p className="muted">Scoped execution history, loaded from a bounded trace index.</p>
          </div>
        </header>

        {liveState !== "live" ? (
          <div className="trace-stream-notice" role="status">
            Live updates are {liveState}. The bounded trace index remains available.
          </div>
        ) : null}

        <TraceToolbar
          backends={backends}
          filters={filters}
          roles={roles}
          onChange={setFilters}
        />

        <section className="trace-index-panel" aria-busy={listLoading}>
          {listError ? <p className="trace-load-error" role="alert">Trace index unavailable: {listError}</p> : null}
          {listLoading && !rows.length ? (
            <p className="trace-table-state muted">Loading traces...</p>
          ) : visibleRows.length ? (
            <TraceTable rows={visibleRows} selectedTraceId={selectedTraceId} onOpen={openTrace} />
          ) : (
            <p className="trace-table-state muted">
              {rows.length ? "No traces match the current filters." : "No traces recorded yet."}
            </p>
          )}
          {hasMore && nextCursor ? (
            <div className="trace-load-more">
              <button className="icon-button" disabled={loadingMore} type="button" onClick={() => void loadMore()}>
                {loadingMore ? "Loading..." : "Load more"}
              </button>
            </div>
          ) : null}
        </section>
      </div>

      {selectedTraceId ? (
        <Suspense fallback={<section aria-label={`Trace ${selectedTraceId}`} className="trace-viewer-route-loading"><p className="muted">Loading trace viewer…</p></section>}>
          <LazyTraceViewer
            actions={viewerActions}
            earlierError={earlierError}
            earlierLoading={earlierLoading}
            earlierSpansError={earlierSpansError}
            earlierSpansLoading={earlierSpansLoading}
            inspectorTab={inspectorTab}
            mode={resolvedViewerMode}
            rawEventId={rawEventId}
            resources={viewerResources}
            selection={{ eventId: selectedEventId, spanId: selectedSpanId, stageId: selectedStageId }}
            spanView={spanView}
            traceId={selectedTraceId}
          />
        </Suspense>
      ) : null}
    </div>
  );
}

function mergeSpanRows(earlier: TraceSpanPage["items"], current: TraceSpanPage["items"]): TraceSpanPage["items"] {
  const merged = new Map<string, TraceSpanPage["items"][number]>();
  for (const span of [...earlier, ...current]) merged.set(span.span_id, span);
  return [...merged.values()];
}

function TraceToolbar({
  backends,
  filters,
  onChange,
  roles,
}: {
  backends: string[];
  filters: TraceFilters;
  onChange: (filters: TraceFilters) => void;
  roles: string[];
}) {
  const moreActive = Boolean(filters.role || filters.backend);
  return (
    <div className="trace-toolbar" aria-label="Trace filters">
      <label className="trace-search-field">
        <Search aria-hidden="true" size={15} />
        <span className="sr-only">Search traces</span>
        <input
          aria-label="Search traces"
          placeholder="Search trace, task, actor"
          type="search"
          value={filters.query}
          onChange={(event) => onChange({ ...filters, query: event.target.value })}
        />
      </label>
      <label className="trace-filter-field">
        <span>Status</span>
        <select
          aria-label="Trace status"
          value={filters.status}
          onChange={(event) => onChange({ ...filters, status: event.target.value as TraceStatusFilter })}
        >
          <option value="all">All</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="blocked">Blocked</option>
          <option value="observed">Observed</option>
        </select>
      </label>
      <label className="trace-filter-field">
        <span>Duration</span>
        <select
          aria-label="Trace duration"
          value={filters.duration}
          onChange={(event) => onChange({ ...filters, duration: event.target.value as TraceDurationFilter })}
        >
          <option value="all">All</option>
          <option value="short">Under 1m</option>
          <option value="medium">1–10m</option>
          <option value="long">Over 10m</option>
          <option value="unknown">Unknown</option>
        </select>
      </label>
      <details className="trace-more-filters" open={moreActive || undefined}>
        <summary>More{moreActive ? " · 1+" : ""}</summary>
        <div className="trace-more-popover">
          <label>
            <span>Role / actor</span>
            <select
              aria-label="Trace role"
              value={filters.role}
              onChange={(event) => onChange({ ...filters, role: event.target.value })}
            >
              <option value="">All roles</option>
              {optionValues(roles, filters.role).map((role) => <option key={role} value={role}>{role}</option>)}
            </select>
          </label>
          <label>
            <span>Backend</span>
            <select
              aria-label="Trace backend"
              value={filters.backend}
              onChange={(event) => onChange({ ...filters, backend: event.target.value })}
            >
              <option value="">All backends</option>
              {optionValues(backends, filters.backend).map((backend) => (
                <option key={backend} value={backend}>{backend}</option>
              ))}
            </select>
          </label>
        </div>
      </details>
    </div>
  );
}

function TraceTable({
  onOpen,
  rows,
  selectedTraceId,
}: {
  onOpen: (traceId: string) => void;
  rows: TraceSummary[];
  selectedTraceId: string;
}) {
  return (
    <div className="trace-table-scroll">
      <table className="trace-table" aria-label="Trace index">
        <thead>
          <tr>
            <th>Status</th>
            <th>Trace / Task</th>
            <th>Started</th>
            <th>Duration</th>
            <th>Events</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const status = traceStatus(row);
            return (
              <tr className={selectedTraceId === row.trace_id ? "selected" : ""} key={row.trace_id}>
                <td><span className={`trace-status trace-status-${status}`}>{status}</span></td>
                <td>
                  <button className="trace-id-link mono" type="button" onClick={() => onOpen(row.trace_id)}>
                    {row.trace_id}
                  </button>
                  <span className="trace-task-label mono">{row.task_ids?.[0] || "No task"}</span>
                </td>
                <td><time dateTime={row.first_ts}>{formatTimestamp(row.first_ts)}</time></td>
                <td>{formatDuration(traceDurationSeconds(row))}</td>
                <td>{row.event_count}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
