import { uiText } from "../settings/uiText";
import { useSettingsPreferences } from "../settings/useSettingsPreferences";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ISSUE_STATE_FILTERS, ISSUE_STATE_STAGES, isIssueStateSelected, issueStateDescription, issueStateLabel, issueStateStage, issueStateStageTotal, nextIssueStageSelection, labelExplanation, } from "./issueTriageModel";
import { githubLabelStyle } from "./IssueTriageLabels";
import { IssueIcon, type IssueIconName } from "./IssueIcon";
export type IssueOrderBy = "created" | "updated" | "name" | "number";
type OpenPanel = {
    id: string;
    mode: "hover" | "pinned";
} | null;
const stageIcons: Record<string, IssueIconName> = { repository: "clock", triage: "dot", decision: "warn", fix: "wrench", delivery: "pr", complete: "shield", stopped: "xcircle" };
const orderLabels: Record<IssueOrderBy, string> = { updated: "Date updated", created: "Date created", number: "Issue number", name: "Issue title" };
interface MultiOption {
    value: string;
    label: ReactNode;
    count?: number;
    tooltip?: string;
}
interface PanelControls {
    panel: OpenPanel;
    enter: (id: string) => void;
    leave: (id: string) => void;
    pin: (id: string) => void;
}
function valuesFor(selected: string[] | null, available: readonly string[]) { return selected === null ? [...available] : selected.filter(value => available.includes(value)); }
function toggle(selected: string[] | null, value: string, available: readonly string[]): string[] | null {
    const active = valuesFor(selected, available);
    const next = active.includes(value) ? active.filter(item => item !== value) : [...active, value];
    return next.length === available.length ? null : next;
}
function Checkbox({ checked, mixed = false }: {
    checked: boolean;
    mixed?: boolean;
}) {
    return <input type="checkbox" aria-hidden="true" tabIndex={-1} readOnly checked={checked} ref={node => { if (node)
        node.indeterminate = mixed; }}/>;
}
function MenuKeyboard(event: ReactKeyboardEvent<HTMLElement>) {
    if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key))
        return;
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
    if (!items.length)
        return;
    event.preventDefault();
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const target = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
    items[target].focus();
}
function MultiFilter({ id, noun, options, selected, onChange, panel, enter, leave, pin }: PanelControls & {
    id: string;
    noun: string;
    options: MultiOption[];
    selected: string[] | null;
    onChange: (next: string[] | null) => void;
}) {
    const values = options.map(option => option.value);
    const active = valuesFor(selected, values);
    const all = values.length > 0 && active.length === values.length;
    const partial = active.length > 0 && !all;
    const open = panel?.id === id;
    const pinned = open && panel.mode === 'pinned';
    return <div data-issue-dropdown={id} className={`multi-filter ${id.replace(/-filter$/, "")} ${id === "source-filter" ? "align-right" : ""} ${open ? 'open' : ''} ${pinned ? 'pinned' : ''}`} onMouseEnter={() => enter(id)} onMouseLeave={() => leave(id)}>
    <div className={`filter-trigger ${active.length ? 'active' : ''}`} data-tooltip={`Filter by ${id.replace(/-filter$/, "")}.`}>
      <span>{noun} <span className="muted">{all ? uiText("All selected") : partial ? `${active.length} selected` : uiText("All")}</span></span>
      <span className="trigger-tail">{active.length ? <span className="filter-count">{active.length}</span> : null}<button className="dropdown-pin" aria-label={`${pinned ? uiText('Close') : 'Pin'} ${noun} filter`} aria-expanded={open} aria-controls={`${id}-menu`} type="button" onClick={() => pin(id)}><IssueIcon name="chevron" className="dropdown-chevron"/></button></span>
    </div>
    <span className="menu-hover-bridge" aria-hidden="true"/>
    <div className="menu" id={`${id}-menu`} role="group" aria-label={`${noun} options`} aria-hidden={!open} ref={node => { if (node)
        node.inert = !open; }} onKeyDown={MenuKeyboard}>
      <div className="menu-header"><strong>{noun}</strong></div>
      <div className="select-all-wrap"><button type="button" className={`check-option select-all-option ${all ? 'selected' : ''}`} role="checkbox" aria-checked={partial ? 'mixed' : all} disabled={!values.length} onClick={() => onChange(all ? [] : null)}><Checkbox checked={all} mixed={partial}/><span>{uiText("Select all")}</span><span className="option-count">{values.length}</span></button></div>
      <div className="option-list">
        {options.map(option => <button key={option.value} type="button" className={`check-option ${active.includes(option.value) ? 'selected' : ''}`} role="checkbox" aria-checked={active.includes(option.value)} data-tooltip={option.tooltip} onClick={() => onChange(toggle(selected, option.value, values))}>
          <Checkbox checked={active.includes(option.value)}/><span>{option.label}</span>{option.count !== undefined ? <span className="option-count">{option.count}</span> : null}
        </button>)}
        {!options.length ? <span className="empty-text">{uiText("No options")}</span> : null}
      </div>
    </div>
  </div>;
}
export function IssueTriageControls({ counts, labelColors, labelCounts, authorCounts, sourceCounts = {}, selectedStates, selectedLabels, selectedAuthors, selectedSources, orderBy, direction, search, clearDisabled, onStates, onLabels, onAuthors, onSources, onOrderBy, onDirection, onSearch, onClear }: {
    counts: Record<string, number>;
    labelColors: Record<string, string>;
    labelCounts: Record<string, number>;
    authorCounts: Record<string, number>;
    sourceCounts?: Record<string, number>;
    selectedStates: string[] | null;
    selectedLabels: string[] | null;
    selectedAuthors: string[] | null;
    selectedSources: string[] | null;
    orderBy: IssueOrderBy;
    direction: "asc" | "desc";
    search: string;
    clearDisabled: boolean;
    onStates: (next: string[] | null) => void;
    onLabels: (next: string[] | null) => void;
    onAuthors: (next: string[] | null) => void;
    onSources: (next: string[] | null) => void;
    onOrderBy: (next: IssueOrderBy) => void;
    onDirection: (next: "asc" | "desc") => void;
    onSearch: (next: string) => void;
    onClear: () => void;
}) {
    const preferences = useSettingsPreferences();
    const [panel, setPanel] = useState<OpenPanel>(null);
    const root = useRef<HTMLDivElement>(null);
    const leaveTimer = useRef<ReturnType<typeof setTimeout>>();
    const suppressed = useRef<string | null>(null);
    const cancelLeave = () => { clearTimeout(leaveTimer.current); leaveTimer.current = undefined; };
    const enter = (id: string) => {
        cancelLeave();
        if (!preferences.hoverOpen) return;
        setPanel(current => current?.mode === 'pinned' || suppressed.current === id ? current : { id, mode: 'hover' });
    };
    const leave = (id: string) => {
        if (suppressed.current === id)
            suppressed.current = null;
        cancelLeave();
        leaveTimer.current = setTimeout(() => setPanel(current => current?.id === id && current.mode === 'hover' ? null : current), preferences.closeDelay);
    };
    const pin = (id: string) => {
        cancelLeave();
        setPanel(current => {
            if (current?.id === id && current.mode === 'pinned') {
                suppressed.current = id;
                return null;
            }
            return { id, mode: 'pinned' };
        });
    };
    useEffect(() => {
        const close = (event: PointerEvent) => {
            const host = event.target instanceof Element ? event.target.closest<HTMLElement>('[data-issue-dropdown]') : null;
            if (!host || !root.current?.contains(host))
                setPanel(null);
        };
        const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') {
            setPanel(current => { suppressed.current = current?.id ?? null; return null; });
        } };
        document.addEventListener('pointerdown', close);
        document.addEventListener('keydown', escape);
        return () => { cancelLeave(); document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
    }, []);
    const panelProps = { panel, enter, leave, pin };
    const stateOptions = useMemo<MultiOption[]>(() => ISSUE_STATE_FILTERS.map(([value, label]) => {
        const stage = issueStateStage(value).id;
        const token = stage === 'repository' ? 'repo' : stage;
        return { value, label: <span className="state-option-label" style={{ color: `var(--${token})` }}><IssueIcon name={stageIcons[stage]}/>{uiText(label)}</span>, count: counts[value] ?? 0, tooltip: issueStateDescription(value) };
    }), [counts, preferences.language]);
    const labelOptions = useMemo(() => Object.keys(labelCounts).sort().map(value => ({ value, count: labelCounts[value], tooltip: labelExplanation(value), label: <span className="label-badge" style={githubLabelStyle(value, labelColors)}>{value}</span> })), [labelColors, labelCounts, preferences.labelPalette]);
    const authorOptions = useMemo(() => Object.keys(authorCounts).sort().map(value => ({ value, count: authorCounts[value], label: <span className="identity"><span className="avatar">{value[0]?.toUpperCase()}</span>{value}</span> })), [authorCounts]);
    const sources = Array.from(new Set(['self_issue', 'github_web', ...Object.keys(sourceCounts)]));
    const sourceOptions = sources.map(value => ({ value, label: value === 'self_issue' ? '/issue' : value === 'github_web' ? 'GitHub' : value === 'unknown' ? 'Unknown' : value, count: sourceCounts[value] ?? 0 }));
    const allStates = ISSUE_STATE_FILTERS.map(([value]) => value);
    const orderOpen = panel?.id === 'order';
    const orderPinned = orderOpen && panel.mode === 'pinned';
    return <div className="issue-controls" ref={root}>
    <div className="stages" aria-label="Issue lifecycle stages">
      {ISSUE_STATE_STAGES.map(stage => {
            const id = `stage-${stage.id}`;
            const selectedCount = stage.states.filter(value => isIssueStateSelected(selectedStates, value)).length;
            const full = selectedCount === stage.states.length;
            const open = panel?.id === id;
            return <section data-issue-dropdown={id} key={id} className={`stage ${stage.id} ${full ? 'full' : selectedCount ? 'partial' : ''} ${open ? 'open' : ''} ${open && panel.mode === 'pinned' ? 'pinned' : ''}`} onMouseEnter={() => enter(id)} onMouseLeave={() => leave(id)}>
          <button className="stage-main" aria-pressed={full} data-tooltip={`${full ? 'Deselect' : 'Select'} ${stage.label} states.`} type="button" onClick={() => onStates(nextIssueStageSelection(selectedStates, stage.states, allStates))}><span><IssueIcon name={stageIcons[stage.id]}/>{uiText(stage.label)}</span><strong>{issueStateStageTotal(stage, counts)}</strong></button>
          <button className="stage-expand" type="button" aria-label={`${stage.label} state menu`} aria-expanded={open} aria-controls={`${id}-menu`} onClick={() => pin(id)}><span>{stage.states.length} state{stage.states.length === 1 ? '' : 's'}</span><IssueIcon name="chevron" className="chev"/></button>
          <span className="stage-hover-bridge" aria-hidden="true"/>
          <div className="stage-menu" id={`${id}-menu`} aria-label={`${stage.label} states`} role="group" aria-hidden={!open} ref={node => { if (node)
                node.inert = !open; }} onKeyDown={MenuKeyboard}>
            {stage.states.map(value => <button className={`stage-option ${isIssueStateSelected(selectedStates, value) ? 'selected' : ''}`} key={value} aria-pressed={isIssueStateSelected(selectedStates, value)} data-tooltip={issueStateDescription(value)} data-tooltip-kind="stage-state" type="button" onClick={() => onStates(toggle(selectedStates, value, allStates))}><span>{issueStateLabel(value)}</span><strong>{counts[value] ?? 0}</strong></button>)}
          </div>
        </section>;
        })}
    </div>
    <div className="filters">
      <div data-issue-dropdown="order" className={`order-wrap ${orderOpen ? 'open' : ''} ${orderPinned ? 'pinned' : ''}`} onMouseEnter={() => enter('order')} onMouseLeave={() => leave('order')}>
        <div className="order-control"><div className="order-trigger" data-tooltip="Choose the sort field."><span><IssueIcon name="sort"/>{uiText(orderLabels[orderBy])}</span><button aria-label={`${orderPinned ? uiText('Close') : 'Pin'} sort menu`} aria-expanded={orderOpen} aria-controls="issue-order-menu" className="dropdown-pin" type="button" onClick={() => pin('order')}><IssueIcon name="chevron" className="dropdown-chevron"/></button></div>
          <div className="order-direction" aria-label="Sort direction">{(['asc', 'desc'] as const).map(value => <button key={value} className={direction === value ? 'active' : ''} aria-pressed={direction === value} aria-label={value === 'asc' ? uiText('Ascending') : uiText('Descending')} data-tooltip={value === 'asc' ? 'Oldest or smallest first.' : 'Newest or largest first.'} type="button" onClick={() => onDirection(value)}><IssueIcon name={value === 'asc' ? 'arrow-up' : 'arrow-down'}/></button>)}</div>
        </div><span className="menu-hover-bridge" aria-hidden="true"/>
        <div id="issue-order-menu" className="menu order-menu" aria-hidden={!orderOpen} ref={node => { if (node)
        node.inert = !orderOpen; }} role="radiogroup" aria-label="Sort field" onKeyDown={MenuKeyboard}>
          <div className="menu-header"><strong>{uiText("Sort by")}</strong><span className="muted order-direction-label">{direction === 'asc' ? uiText('Ascending') : uiText('Descending')}</span></div>
          {(Object.entries(orderLabels) as [
        IssueOrderBy,
        string
    ][]).map(([value, label]) => <button className={`order-option ${orderBy === value ? 'selected' : ''}`} role="radio" aria-checked={orderBy === value} key={value} type="button" onClick={() => onOrderBy(value)}><span className="radio"/><span>{uiText(label)}</span></button>)}
        </div>
      </div>
      <label className="search"><IssueIcon name="search"/><input aria-label="Search issues" placeholder="Search title, number, author, or label" value={search} onChange={event => onSearch(event.target.value)}/></label>
      <MultiFilter {...panelProps} id="state-filter" noun="State" options={stateOptions} selected={selectedStates} onChange={onStates}/>
      <MultiFilter {...panelProps} id="label-filter" noun="Label" options={labelOptions} selected={selectedLabels} onChange={onLabels}/>
      <MultiFilter {...panelProps} id="contributor-filter" noun="Contributor" options={authorOptions} selected={selectedAuthors} onChange={onAuthors}/>
      <MultiFilter {...panelProps} id="source-filter" noun={uiText("Source")} options={sourceOptions} selected={selectedSources} onChange={onSources}/>
      <button className="btn clear-btn" data-tooltip={clearDisabled ? 'No filters to clear.' : 'Clear search and all selections.'} disabled={clearDisabled} type="button" onClick={onClear}><IssueIcon name="clear"/>{uiText("Clear")}</button>
    </div>
  </div>;
}
