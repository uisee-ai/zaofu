import { uiText } from "../settings/uiText";
import { useSettingsPreferences, getSettingsPreferences, formatSettingsTime } from "../settings/useSettingsPreferences";
import { useIssueRefresh } from "./useIssueRefresh";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getIssueTriage, getIssueTriageDetail, getIssueTriageSummary, peekIssueTriagePage, peekIssueTriageSummary, peekIssueTriageDetail, startIssueTriage, cancelQueuedIssueTriage } from "../api/client";
import type { IssueTriageDetail as Detail, IssueTriageItem, IssueTriagePageData, IssueTriageSummary } from "../api/types";
import { ISSUE_STATE_FILTERS, canManageIssueRun, effectiveIssueState, issueStateStage, issueStateDescription, issueSelectionQueryValues, issueSyncPresentation, issueTriageSourceLabel } from "./issueTriageModel";
import { IssueTriageControls, type IssueOrderBy } from "./IssueTriageControls";
import { IssueNotificationStack, useIssueNotifications } from "./IssueTriageNotifications";
import { IssueLabelList } from "./IssueTriageLabels";
import { IssueIcon, type IssueIconName } from "./IssueIcon";
import { IssueStateValue } from "./IssueStateBadge";
import { IssueTooltipLayer } from "./IssueOverlay";
import { IssueAuthor } from "./IssueAuthor";
import { issueOpenedAgo, issueCreatedAt } from "./issueTimePresentation";
import { StartTriageDialog, CancelQueuedDialog } from "./IssueTriageDialogs";
import { IssueTriageRunDialog } from "./IssueTriageRunDialog";
import { IssueTriageDetail } from "./IssueTriageDetail";
export type TriageTab = "github" | "gitlab" | "runtime";
export interface IssueNavigation {
    onOpenTask: (taskId: string) => void;
    onOpenProjection: (kind: "run" | "trace", id: string) => void;
}
export function IssueTriagePage({ projectId, runtimeInterventions, tab, onTabChange, onOpenTask, onOpenProjection }: IssueNavigation & {
    projectId: string;
    runtimeInterventions: ReactNode;
    tab: TriageTab;
    onTabChange: (tab: TriageTab) => void;
}) {
    const prefs = useSettingsPreferences();
    return <section className="issue-triage-host" data-issue-motion={prefs.motion} style={{ "--issue-visible-rows": prefs.visibleRows } as React.CSSProperties}>
    <div className="issue-workspace issue-navigation"><div className="tabs" role="tablist" aria-label="Triage domain">{([['github', 'GITHUB ISSUE'], ['gitlab', 'GITLAB ISSUE'], ['runtime', 'RUNTIME INTERVENTIONS']] as const).map(([value, label]) => <button key={value} className={`tab ${tab === value ? 'active' : ''}`} role="tab" aria-selected={tab === value} type="button" onClick={() => onTabChange(value)}>{uiText(label)}</button>)}</div></div>
    {tab === 'github' ? <div className="issue-workspace"><IssuesTab key={projectId} projectId={projectId} onOpenTask={onOpenTask} onOpenProjection={onOpenProjection}/></div> : tab === 'gitlab' ? <section className="issue-triage-placeholder" aria-label="GitLab Issue"/> : runtimeInterventions}
  </section>;
}
const allStates = ISSUE_STATE_FILTERS.map(([value]) => value);
const stageIcons: Record<string, IssueIconName> = { repository: 'clock', triage: 'dot', decision: 'warn', fix: 'wrench', delivery: 'pr', complete: 'shield', stopped: 'xcircle' };
function timestamp(value?: string) { return formatSettingsTime(value); }
type IssueRouteState = {
    query: string;
    states: string[] | null;
    labels: string[] | null;
    authors: string[] | null;
    sources: string[] | null;
    orderBy: IssueOrderBy;
    direction: 'asc' | 'desc';
    cursor: number;
    issueNumber: number | null;
};
const EMPTY_ISSUE_ROUTE: IssueRouteState = {
    query: '', states: [], labels: [], authors: [], sources: [], orderBy: 'updated', direction: 'desc', cursor: 0, issueNumber: null,
};
const ISSUE_ROUTE_KEYS = ['q', 'states', 'labels', 'authors', 'sources', 'order_by', 'order_direction', 'cursor', 'issue'] as const;
function parseIssueRouteState(params: URLSearchParams): IssueRouteState {
    const parseList = (key: string): string[] | null => {
        const raw = params.get(key);
        if (!raw)
            return [];
        try {
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed.map(value => String(value)).filter(Boolean) : [];
        }
        catch {
            return raw.split(',').map(value => value.trim()).filter(Boolean);
        }
    };
    const defaults = getSettingsPreferences();
    const parsedIssue = Number(params.get('issue') || '');
    const parsedCursor = Number(params.get('cursor') || '0');
    const requestedOrder = params.get('order_by');
    const orderBy: IssueOrderBy = requestedOrder === 'created' || requestedOrder === 'number' || requestedOrder === 'updated' || requestedOrder === 'name'
        ? requestedOrder : defaults.sortField;
    return {
        query: params.get('q') || '',
        states: parseList('states'),
        labels: parseList('labels'),
        authors: parseList('authors'),
        sources: parseList('sources'),
        orderBy,
        direction: params.has('order_direction') ? (params.get('order_direction') === 'asc' ? 'asc' : 'desc') : defaults.sortDirection,
        cursor: Number.isFinite(parsedCursor) && parsedCursor >= 0 ? parsedCursor : 0,
        issueNumber: Number.isInteger(parsedIssue) && parsedIssue > 0 ? parsedIssue : null,
    };
}
function readIssueRouteState(projectId: string): IssueRouteState {
    if (typeof window === 'undefined')
        return EMPTY_ISSUE_ROUTE;
    const url = new URL(window.location.href);
    // App-level navigation replaces the query string when changing pages. Keep
    // a second, project-scoped copy so returning to Triage restores the exact
    // search, filters, sort order, page and expanded Issue.
    if (ISSUE_ROUTE_KEYS.some(key => url.searchParams.has(key)))
        return parseIssueRouteState(url.searchParams);
    try {
        const raw = getSettingsPreferences().rememberFilters ? window.sessionStorage.getItem(`zf.issue-triage.route:${projectId}`) : null;
        if (raw) {
            const parsed = JSON.parse(raw) as Record<string, unknown>;
            const params = new URLSearchParams();
            if (typeof parsed.query === 'string' && parsed.query)
                params.set('q', parsed.query);
            for (const [key, value] of [['states', parsed.states], ['labels', parsed.labels], ['authors', parsed.authors], ['sources', parsed.sources]] as const) {
                if (Array.isArray(value) && value.length)
                    params.set(key, JSON.stringify(value));
            }
            if (parsed.orderBy === 'created' || parsed.orderBy === 'updated' || parsed.orderBy === 'name' || parsed.orderBy === 'number')
                params.set('order_by', parsed.orderBy);
            if (parsed.direction === 'asc' || parsed.direction === 'desc')
                params.set('order_direction', parsed.direction);
            if (typeof parsed.cursor === 'number' && parsed.cursor > 0)
                params.set('cursor', String(parsed.cursor));
            if (typeof parsed.issueNumber === 'number' && parsed.issueNumber > 0)
                params.set('issue', String(parsed.issueNumber));
            return parseIssueRouteState(params);
        }
    }
    catch {
        // Storage can be unavailable in privacy mode or contain an old shape.
    }
    return { ...EMPTY_ISSUE_ROUTE, orderBy: getSettingsPreferences().sortField, direction: getSettingsPreferences().sortDirection };
}
function issueFilters(route: IssueRouteState, limit: number) {
    const states = issueSelectionQueryValues(route.states, allStates);
    return { q: route.query.trim(), states: states ? JSON.stringify(states) : '', labels: route.labels?.length ? JSON.stringify(route.labels) : '', authors: route.authors?.length ? JSON.stringify(route.authors) : '', sources: route.sources?.length ? JSON.stringify(route.sources) : '', order_by: route.orderBy, order_direction: route.direction, cursor: route.cursor, limit };
}
function IssuesTab({ projectId, onOpenTask, onOpenProjection }: IssueNavigation & {
    projectId: string;
}) {
    const prefs = useSettingsPreferences();
    const initialRoute = useMemo(() => readIssueRouteState(projectId), [projectId]);
    const [summary, setSummary] = useState<IssueTriageSummary | null>(() => peekIssueTriageSummary(projectId));
    const [page, setPage] = useState<IssueTriagePageData | null>(() => peekIssueTriagePage(projectId, issueFilters(initialRoute, prefs.pageSize)));
    const [detail, setDetail] = useState<Detail | null>(() => initialRoute.issueNumber === null ? null : peekIssueTriageDetail(projectId, initialRoute.issueNumber));
    const [query, setQuery] = useState(initialRoute.query);
    const [appliedQuery, setAppliedQuery] = useState(initialRoute.query.trim());
    const [selectedStates, setSelectedStates] = useState<string[] | null>(initialRoute.states);
    const [selectedLabels, setSelectedLabels] = useState<string[] | null>(initialRoute.labels);
    const [selectedAuthors, setSelectedAuthors] = useState<string[] | null>(initialRoute.authors);
    const [selectedSources, setSelectedSources] = useState<string[] | null>(initialRoute.sources);
    const [orderBy, setOrderBy] = useState<IssueOrderBy>(initialRoute.orderBy);
    const [direction, setDirection] = useState<'asc' | 'desc'>(initialRoute.direction);
    const [cursor, setCursor] = useState(initialRoute.cursor);
    const [selectedIssueNumber, setSelectedIssueNumber] = useState<number | null>(initialRoute.issueNumber);
    const [loading, setLoading] = useState(!page);
    const { pending: refreshPending, run: runRefresh } = useIssueRefresh(projectId);
    const refreshing = refreshPending || summary?.sync.status === "syncing";
    const [error, setError] = useState('');
    const [now, setNow] = useState(Date.now);
    const [startOpen, setStartOpen] = useState(false);
    const [cancelOpen, setCancelOpen] = useState(false);
    const [manageOpen, setManageOpen] = useState(false);
    const [actionIssue, setActionIssue] = useState<IssueTriageItem | null>(null);
    const [reference, setReference] = useState('');
    const [starting, setStarting] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const [startError, setStartError] = useState('');
    const loadRequest = useRef(0);
    const detailRequest = useRef(0);
    const selectedNumber = useRef<number | null>(null);
    const mounted = useRef(true);
    const { items: notifications, notify, dismiss } = useIssueNotifications();
    const recoverySeen = useRef('');
    useEffect(() => {
        const next = detail ? `${detail.issue.issue_key}:${detail.issue.workflow?.state}` : '';
        const previous = recoverySeen.current; recoverySeen.current = next;
        if (previous && next !== previous && detail && previous.startsWith(`${detail.issue.issue_key}:`) && ['triage_recovering','fix_recovering'].includes(detail.issue.workflow?.state || ''))
            notify({title:uiText('Automatic recovery'),message:uiText('ZaoFu is recovering the selected workflow.'),tone:'info',category:'recovery'});
    }, [detail, notify]);
    const intakeError = useRef('');
    const routeHydrated = useRef(false);
    useEffect(() => { selectedNumber.current = selectedIssueNumber; }, [selectedIssueNumber]);
    useEffect(() => {
        if (typeof window === 'undefined')
            return;
        const routeSnapshot = { query: query.trim(), states: selectedStates ?? [], labels: selectedLabels ?? [], authors: selectedAuthors ?? [], sources: selectedSources ?? [], orderBy, direction, cursor, issueNumber: selectedIssueNumber };
        try {
            if (prefs.rememberFilters) window.sessionStorage.setItem(`zf.issue-triage.route:${projectId}`, JSON.stringify(routeSnapshot));
        }
        catch {
            // URL state remains the fallback when session storage is blocked.
        }
        if (!routeHydrated.current) {
            routeHydrated.current = true;
            return;
        }
        const url = new URL(window.location.href);
        const params = url.searchParams;
        const setOrDelete = (key: string, value: string) => value ? params.set(key, value) : params.delete(key);
        setOrDelete('q', query.trim());
        setOrDelete('states', selectedStates?.length ? JSON.stringify(selectedStates) : '');
        setOrDelete('labels', selectedLabels?.length ? JSON.stringify(selectedLabels) : '');
        setOrDelete('authors', selectedAuthors?.length ? JSON.stringify(selectedAuthors) : '');
        setOrDelete('sources', selectedSources?.length ? JSON.stringify(selectedSources) : '');
        setOrDelete('order_by', orderBy);
        setOrDelete('order_direction', direction);
        setOrDelete('cursor', cursor > 0 ? String(cursor) : '');
        setOrDelete('issue', selectedIssueNumber ? String(selectedIssueNumber) : '');
        window.history.replaceState(window.history.state, '', `${url.pathname}${params.toString() ? `?${params}` : ''}${url.hash}`);
    }, [projectId, query, selectedStates, selectedLabels, selectedAuthors, selectedSources, orderBy, direction, cursor, selectedIssueNumber, prefs.rememberFilters]);
    useEffect(() => {
        const message = summary?.intake?.status === 'failed'
            ? summary.intake.error || 'Background Issue processing failed. Refresh to retry.' : '';
        if (message && message !== intakeError.current)
            notify({ title: 'Issue evidence processing failed', message, tone: 'danger', persistent: true });
        intakeError.current = message;
    }, [summary?.intake, notify]);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; loadRequest.current++; detailRequest.current++; }; }, []);
    useEffect(() => {
        // One clock for the list, not one interval per row. Update on returning
        // to the tab as well; updating metadata never recreates the detail pane.
        const tick = () => setNow(Date.now());
        const timer = window.setInterval(tick, 60_000);
        document.addEventListener('visibilitychange', tick);
        return () => { clearInterval(timer); document.removeEventListener('visibilitychange', tick); };
    }, []);
    const filters = useMemo(() => {
        return issueFilters({ query: appliedQuery, states: selectedStates, labels: selectedLabels, authors: selectedAuthors, sources: selectedSources, orderBy, direction, cursor, issueNumber: null }, prefs.pageSize);
    }, [appliedQuery, selectedStates, selectedLabels, selectedAuthors, selectedSources, orderBy, direction, cursor, prefs.pageSize]);
    const load = useCallback(async () => {
        const request = ++loadRequest.current;
        const current = () => request === loadRequest.current && mounted.current;
        // Publish each response immediately: a slow summary cannot hide rows.
        // GETs always revalidate, sharing only concurrent requests for the same key.
        const summaryRequest = getIssueTriageSummary(projectId).then(next => {
            if (current()) setSummary(next);
        });
        const pageRequest = getIssueTriage(projectId, filters).then(nextPage => {
            if (!current()) return;
            setPage(nextPage);
            // A result can shrink after cancellation. Move only the list to its last valid page.
            if (!nextPage.items.length && cursor > 0 && nextPage.total > 0)
                setCursor(Math.floor((nextPage.total - 1) / prefs.pageSize) * prefs.pageSize);
        }).finally(() => { if (current()) setLoading(false); });
        const results = await Promise.allSettled([summaryRequest, pageRequest]);
        if (!current()) return;
        const failure = results.find(result => result.status === 'rejected');
        if (failure?.status === 'rejected') throw failure.reason;
    }, [projectId, filters, cursor, prefs.pageSize]);
    const updateDetail = useCallback(async (number: number) => {
        const request = ++detailRequest.current;
        const next = await getIssueTriageDetail(projectId, number);
        if (mounted.current && request === detailRequest.current && selectedNumber.current === number)
            setDetail(next);
    }, [projectId]);
    useEffect(() => {
        let active = true;
        void load().then(() => { if (active)
            setError(''); }).catch(cause => {
            if (!active)
                return;
            const message = cause instanceof Error ? cause.message : String(cause);
            setError(message);
            notify({ title: 'Issue mirror unavailable', message, tone: 'danger', persistent: true });
        }).finally(() => { if (active)
            setLoading(false); });
        return () => { active = false; };
    }, [load, notify]);
    const wasRefreshing = useRef(refreshPending);
    useEffect(() => {
        if (wasRefreshing.current && !refreshPending)
            void load().catch(() => undefined);
        wasRefreshing.current = refreshPending;
    }, [load, refreshPending]);
    useEffect(() => {
        if (!prefs.autoRefresh) return;
        let active = true, pending = false;
        let timer: ReturnType<typeof setTimeout>;
        const tick = async () => {
            if (!active || pending) return;
            clearTimeout(timer);
            pending = true;
            try { await Promise.all([load(), selectedNumber.current === null ? Promise.resolve() : updateDetail(selectedNumber.current)]); }
            catch { /* Keep last good state. Explicit Refresh displays request failures. */ }
            finally { pending = false; }
            if (active) timer = setTimeout(() => { void tick(); }, prefs.refreshSeconds * 1000);
        };
        const visible = () => { if (!document.hidden && prefs.refreshOnReturn) void tick(); };
        timer = setTimeout(() => { void tick(); }, prefs.refreshSeconds * 1000);
        document.addEventListener('visibilitychange', visible);
        return () => { active = false; clearTimeout(timer); document.removeEventListener('visibilitychange', visible); };
    }, [load, updateDetail, prefs.autoRefresh, prefs.refreshSeconds, prefs.refreshOnReturn]);
    const priorPageSize = useRef(prefs.pageSize);
    useEffect(() => { if (priorPageSize.current !== prefs.pageSize) { priorPageSize.current = prefs.pageSize; setCursor(0); } }, [prefs.pageSize]);
    useEffect(() => {
        if (!prefs.rememberFilters) { try { sessionStorage.removeItem(`zf.issue-triage.route:${projectId}`); } catch {} }
    }, [prefs.rememberFilters, projectId]);
    useEffect(() => { const timer = setTimeout(() => { setAppliedQuery(query.trim()); setCursor(0); }, 250); return () => clearTimeout(timer); }, [query]);
    useEffect(() => {
        if (selectedIssueNumber !== null)
            void updateDetail(selectedIssueNumber).catch(() => undefined);
    }, [selectedIssueNumber, updateDetail]);
    const closeDetail = () => { selectedNumber.current = null; setSelectedIssueNumber(null); detailRequest.current++; setDetail(null); };
    const openDetail = async (number: number) => {
        if (selectedNumber.current === number) {
            closeDetail();
            return;
        }
        selectedNumber.current = number;
        setSelectedIssueNumber(number);
        setDetail(peekIssueTriageDetail(projectId, number));
        try {
            await updateDetail(number);
        }
        catch (cause) {
            if (selectedNumber.current !== number)
                return;
            selectedNumber.current = detail?.issue.number ?? null;
            notify({ title: 'Issue could not be opened', message: String(cause instanceof Error ? cause.message : cause), tone: 'danger' });
        }
    };
    const refresh = async () => {
        if (refreshing)
            return;
        try {
            await runRefresh();
            if (!mounted.current) return;
            await load();
            if (selectedNumber.current !== null)
                await updateDetail(selectedNumber.current);
            setError('');
            notify({ title: 'Issue mirror refreshed', message: 'The latest Issue metadata is now available.', tone: 'success' });
        }
        catch (cause) {
            if (!mounted.current) return;
            const message = cause instanceof Error ? cause.message : String(cause);
            setError(message);
            notify({ title: 'Refresh failed', message, tone: 'danger' });
        }
    };
    const refreshAfterAction = async (number: number) => {
        try {
            await load();
            if (selectedNumber.current === number)
                await updateDetail(number);
        }
        catch (cause) {
            if (mounted.current)
                notify({ title: 'Action completed; refresh needed', message: cause instanceof Error ? cause.message : String(cause), tone: 'warning', persistent: true });
        }
    };
    const patchIssueWorkflow = useCallback((number: number, workflow: Record<string, unknown> | null) => {
        const patch = (item: IssueTriageItem): IssueTriageItem => item.number === number
            ? { ...item, workflow: { ...item.workflow, ...workflow } as IssueTriageItem['workflow'] }
            : item;
        setPage(current => current ? { ...current, items: current.items.map(patch) } : current);
        setDetail(current => current?.issue.number === number
            ? { ...current, issue: patch(current.issue) }
            : current);
        setActionIssue(current => current?.number === number ? patch(current) : current);
    }, []);
    const onUpdated = async (number: number, message: string) => {
        if (mounted.current && message)
            notify({ title: 'Issue updated', message, tone: 'success' });
        await refreshAfterAction(number);
    };
    const openStart = () => { setActionIssue(detail?.issue ?? null); setReference(detail ? `#${detail.issue.number}` : ''); setStartError(''); setStartOpen(true); };
    const queueTriage = async () => {
        if (starting)
            return;
        if (!reference.trim()) {
            setStartError('Enter a GitHub Issue URL or number.');
            return;
        }
        setStarting(true);
        setStartError('');
        try {
            const result = await startIssueTriage(projectId, reference.trim());
            if (!mounted.current)
                return;
            setStartOpen(false);
            selectedNumber.current = result.issue_number;
            setSelectedIssueNumber(result.issue_number);
            if (result.issue) {
                const updatedIssue = result.issue;
                setPage(current => current ? {
                    ...current,
                    items: current.items.map(item => item.number === updatedIssue.number ? updatedIssue : item),
                } : current);
                setDetail(current => current?.issue.number === updatedIssue.number
                    ? { ...current, issue: updatedIssue }
                    : current);
                setActionIssue(updatedIssue);

            }
            else {
                // The queue event is already durable when the API responds.
                // Update the visible row immediately; the subsequent read
                // reconciles the full projection without making the icon wait
                // for the background mirror/label work.
                patchIssueWorkflow(result.issue_number, {
                    state: 'triage_queued',
                    triage_id: result.workflow_run_id || '',
                    queued_cancel_available: true,
                    source_revision: result.source_revision || '',
                    workflow_project_id: result.workflow_project_id || projectId,
                    workflow_project_name: result.workflow_project_name,
                    execution_project_root: result.execution_project_root,
                });
            }
            notify({ title: result.queued ? uiText('Triage queued') : 'Triage already queued', message: result.queued ? `Issue #${result.issue_number} was added to the Triage queue.` : `Issue #${result.issue_number} is already queued for this revision.`, tone: result.queued ? 'success' : 'info' });
            // The response already contains the durable queue projection. Do
            // not immediately reload the whole list here: a concurrent
            // pre-action read can otherwise overwrite the optimistic row
            // until the next auto-refresh tick.
        }
        catch (cause) {
            const message = cause instanceof Error ? cause.message : String(cause);
            setStartError(message);
            notify({ title: uiText('Triage could not be started'), message, tone: 'danger' });
        }
        finally {
            if (mounted.current)
                setStarting(false);
        }
    };
    const cancelQueued = async () => {
        if (!actionIssue || cancelling)
            return;
        setCancelling(true);
        const noun = actionIssue.workflow?.state === 'fix_queued' ? 'Fix' : 'Triage';
        try {
            const result = await cancelQueuedIssueTriage(projectId, actionIssue.number, actionIssue.workflow?.triage_id);
            setCancelOpen(false);
            patchIssueWorkflow(actionIssue.number, {
                state: actionIssue.workflow?.state === 'fix_queued' ? 'fix_cancelled' : 'triage_cancelled',
                triage_id: '',
                queued_cancel_available: false,
                source_revision: actionIssue.workflow?.source_revision || '',
            });
            const sync = result.lifecycle_label_sync;
            const pending = !!sync && sync.status !== 'reconciled' && sync.status !== 'superseded';
            notify({ title: `Queued ${noun} cancelled`, message: pending
                ? uiText('Cancellation saved. GitHub label synchronization is pending; check GitHub access and refresh to retry.')
                : `Issue #${actionIssue.number} was removed from the ${noun} queue.`, tone: pending ? 'warning' : 'success' });
            // The local cancellation projection above is authoritative for
            // this response. The regular auto-refresh will reconcile counts
            // and cross-project ownership without delaying the icon update.
        }
        catch (cause) {
            notify({ title: uiText('Cancel failed'), message: cause instanceof Error ? cause.message : String(cause), tone: 'danger' });
        }
        finally {
            if (mounted.current)
                setCancelling(false);
        }
    };
    const selectLabel = (label: string) => {
        setSelectedLabels(current => {
            const available = Object.keys(summary?.labels ?? {});
            const active = current === null ? available : current;
            const next = active.includes(label) ? active.filter(value => value !== label) : [...active, label];
            return available.length && next.length === available.length ? null : next;
        });
        setCursor(0);
    };
    const labelColors = useMemo(() => ({ ...(summary?.label_colors ?? {}), ...Object.assign({}, ...(page?.items ?? []).map(item => item.label_colors ?? {})) }), [summary, page]);
    const workflowState = detail?.issue.workflow?.state ?? '';
    const queuedWithoutLocalRun = !!detail && ['triage_queued', 'fix_queued'].includes(workflowState)
        && (!detail.issue.workflow?.triage_id || detail.issue.workflow.queued_cancel_available === false);
    const sync = issueSyncPresentation(
        refreshing ? 'syncing' : summary?.sync.status ?? 'never',
        refreshing ? '' : error || summary?.sync.error || '',
    );
    const clearDisabled = !query && [selectedStates, selectedLabels, selectedAuthors, selectedSources].every(selection => selection !== null && !selection.length);
    return <main className="page">
    <header className="header"><p className="description">{uiText("Read-only in ZaoFu. Edit labels, state, assignees, or content on GitHub.")}</p><div className="toolbar">
      <div className="sync"><span className={`sync-dot ${sync.tone}`} role="img" aria-label={sync.label} data-tooltip={sync.label}/><span>{summary?.sync.last_success_at ? `${uiText("Last synced")} ${timestamp(summary.sync.last_success_at)}` : uiText('Not synchronized yet')}</span></div>
      <button className="btn" type="button" disabled={refreshing} data-tooltip="Refresh Issue metadata and comments." onClick={() => void refresh()}><IssueIcon name="refresh" className={refreshing ? 'spinning' : ''}/>{uiText("Refresh")}</button>
      {detail && canManageIssueRun(workflowState) ? <button className="btn primary" type="button" data-tooltip="Pause, resume, or cancel this Run." onClick={() => { setActionIssue(detail.issue); setManageOpen(true); }}><IssueIcon name="sliders"/>{uiText("Manage Run")}</button> : detail && ['triage_queued', 'fix_queued'].includes(workflowState) && !queuedWithoutLocalRun ? <button className="btn danger" type="button" disabled={cancelling} data-tooltip="Cancel this queued request." onClick={() => { setActionIssue(detail.issue); setCancelOpen(true); }}><IssueIcon name="xcircle"/>{uiText("Cancel queued ")}{workflowState === 'fix_queued' ? 'Fix' : 'Triage'}</button> : queuedWithoutLocalRun ? <button className="btn" type="button" disabled data-tooltip="No cancellable queued request in this project. Refresh or switch to the project that queued it."><IssueIcon name="clock"/>{uiText("No local queued request")}</button> : <button className="btn primary" type="button" disabled={starting} data-tooltip="Queue read-only Triage for an Issue." onClick={openStart}><IssueIcon name="play"/>{uiText("Start Triage")}</button>}
      {summary ? <><a className="repo-action" href={summary.repository_url} target="_blank" rel="noopener noreferrer" data-tooltip="Open the GitHub repository."><span className="repo-label"><IssueIcon name="github"/>{uiText("View Repo")}</span><span className="repo-stat"><IssueIcon name="star"/>{new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(summary.sync.star_count ?? 0)}</span></a><a className="btn" href={summary.new_issue_url} target="_blank" rel="noopener noreferrer" data-tooltip="Create an Issue on GitHub.">{uiText("New issue")}<IssueIcon name="external"/></a></> : null}
    </div></header>
    <IssueTriageControls counts={summary?.states ?? {}} labelColors={labelColors} labelCounts={summary?.labels ?? {}} authorCounts={summary?.authors ?? {}} sourceCounts={summary?.sources ?? {}} selectedStates={selectedStates} selectedLabels={selectedLabels} selectedAuthors={selectedAuthors} selectedSources={selectedSources} orderBy={orderBy} direction={direction} search={query} clearDisabled={clearDisabled} onStates={next => { setSelectedStates(next); setCursor(0); }} onLabels={next => { setSelectedLabels(next); setCursor(0); }} onAuthors={next => { setSelectedAuthors(next); setCursor(0); }} onSources={next => { setSelectedSources(next); setCursor(0); }} onOrderBy={next => { setOrderBy(next); setCursor(0); }} onDirection={next => { setDirection(next); setCursor(0); }} onSearch={setQuery} onClear={() => { setQuery(''); setAppliedQuery(''); setSelectedStates([]); setSelectedLabels([]); setSelectedAuthors([]); setSelectedSources([]); setCursor(0); closeDetail(); notify({title:uiText('Filters cleared'),message:uiText('Search and all selections were cleared.'),tone:'info',duration:3000}); }}/>
    <section className={`content ${detail ? 'with-detail' : ''}`}>
      <div className="list" aria-label="GitHub issues" aria-busy={loading}><div className="list-scroll">
        {loading ? <p className="empty-text">{uiText("Loading Issue mirror…")}</p> : !page?.items.length ? <p className="empty-text">{error ? uiText('Could not load Issues. Use Refresh to try again.') : uiText('No matching GitHub Issues.')}</p> : null}
        {page?.items.map(item => {
            const state = effectiveIssueState(item.github_state, item.workflow?.state);
            const stage = issueStateStage(state).id;
            const color = stage === 'repository' ? 'repo' : stage;
            return <div data-issue-number={item.number} className={`issue-row ${detail?.issue.number === item.number ? 'selected' : ''}`} key={item.issue_key || item.number} role="button" tabIndex={0} aria-pressed={detail?.issue.number === item.number} aria-label={`Open Issue ${item.number}`} onClick={() => void openDetail(item.number)} onKeyDown={event => { if (event.target !== event.currentTarget)
                return; if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                void openDetail(item.number);
            } }}>
            <span className="row-icon" style={{ color: `var(--${color})` }} data-tooltip={issueStateDescription(state)}><IssueIcon name={stageIcons[stage]} size="lg"/></span>
            <div className="row-main"><strong className="issue-title">{item.title}</strong><div className="row-meta"><span>#{item.number} ·</span><IssueAuthor login={item.author_login} avatarUrl={item.author_avatar_url} stats={summary?.author_states?.[item.author_login]}/><span>· {issueTriageSourceLabel(item.source)} · <time dateTime={issueCreatedAt(item.created_at).dateTime} data-tooltip={`Created at ${issueCreatedAt(item.created_at).text}`}>{prefs.timestamp === 'relative' ? issueOpenedAgo(item.created_at, now) : formatSettingsTime(prefs.timestamp === 'updated' ? item.updated_at : item.created_at)}</time></span></div><IssueLabelList labels={item.labels} colors={item.label_colors} selected={selectedLabels} onSelect={selectLabel}/></div>
            <span className="row-state"><IssueStateValue state={state}/></span>
          </div>;
        })}
      </div><footer className={`pagination ${!page || page.total <= prefs.pageSize ? 'single-page' : ''}`}><button className="btn" type="button" disabled={!page || page.cursor === 0} onClick={() => setCursor(Math.max(0, cursor - prefs.pageSize))}>{uiText("Previous")}</button><span className="range">{page ? page.total <= prefs.pageSize ? `${page.total} issues · all shown` : `${page.cursor + (page.items.length ? 1 : 0)}–${page.cursor + page.items.length} of ${page.total}` : '0 issues'}</span><button className="btn" type="button" disabled={page?.next_cursor == null} onClick={() => setCursor(page?.next_cursor ?? cursor)}>{uiText("Next")}</button></footer></div>
      {detail ? <IssueTriageDetail projectId={projectId} detail={detail} selectedLabels={selectedLabels} authorStates={summary?.author_states ?? {}} onClose={closeDetail} onLabelSelect={selectLabel} onOpenTask={onOpenTask} onOpenProjection={onOpenProjection} onNotification={notify} onUpdated={notice => onUpdated(detail.issue.number, notice)}/> : null}
    </section>
    <StartTriageDialog open={startOpen} error={startError} reference={reference} issue={actionIssue} busy={starting} repository={summary?.repository ?? ''} onChange={setReference} onClose={() => { if (!starting)
        setStartOpen(false); }} onSubmit={() => void queueTriage()}/>
    <CancelQueuedDialog open={cancelOpen} issue={actionIssue} busy={cancelling} onClose={() => { if (!cancelling)
        setCancelOpen(false); }} onConfirm={() => void cancelQueued()}/>
    <IssueTriageRunDialog open={manageOpen} projectId={actionIssue?.workflow?.workflow_project_id || projectId} issue={actionIssue} onClose={() => setManageOpen(false)} onNotification={notify} onUpdated={notice => actionIssue ? onUpdated(actionIssue.number, notice) : Promise.resolve()}/>
    <IssueNotificationStack items={notifications} onDismiss={dismiss}/><IssueTooltipLayer />
  </main>;
}
