import { ArrowDownUp, Columns3, Filter, Globe2, List, Plus, Search, Table2, X } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import type { ViewMode } from "../../app/sharedTypes";
import { useUiPreferences } from "../../app/UiPreferences";
import { BOARD_COLUMNS } from "./board";
import { TASK_FOCUS_OPTIONS } from "./taskToolbarSelection";
import type { TaskFocus } from "./taskToolbarSelection";
import type { TaskGroup, TaskSort } from "./taskViewModel";
export interface TaskToolbarProps {
  textFilter: string; setTextFilter: (value: string) => void;
  statusFilter: string; setStatusFilter: (value: string) => void;
  assigneeFilter: string; setAssigneeFilter: (value: string) => void; assignees: string[];
  skillFilter: string; setSkillFilter: (value: string) => void; skills: string[];
  priorityFilter: string; setPriorityFilter: (value: string) => void;
  quickFilter: string; setQuickFilter: (value: string) => void;
  viewMode: ViewMode; setViewMode: (value: ViewMode) => void;
  sort: TaskSort; setSort: (value: TaskSort) => void;
  descending: boolean; setDescending: (value: boolean) => void;
  group: TaskGroup; setGroup: (value: TaskGroup) => void;
  activeFocus: TaskFocus | null; onFocus: (value: TaskFocus) => void;
  onNewTask: () => void; count: number; projectName: string; accessControl?: ReactNode;
}
export function TaskToolbar(p: TaskToolbarProps) {
  const { t } = useUiPreferences();
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filters = [
    { label: "Status", value: p.statusFilter, set: p.setStatusFilter, options: BOARD_COLUMNS.map((c) => [c.id, c.title]) },
    { label: "Owner", value: p.assigneeFilter, set: p.setAssigneeFilter, options: p.assignees.map((s) => [s, s]) },
    { label: "Skill", value: p.skillFilter, set: p.setSkillFilter, options: p.skills.map((s) => [s, s]) },
    { label: "Priority", value: p.priorityFilter, set: p.setPriorityFilter, options: [0, 1, 2, 3, 4, 5].map((n) => [String(n), `P${n}`]) },
    { label: "Signal", value: p.quickFilter, set: p.setQuickFilter, options: [["focused", "Focused work"], ["ready", "Ready"], ["blocked", "Blocked"], ["failed", "Failed"]] },
  ];
  const active = filters.filter((filter) => filter.value !== "all");
  return <>
    <header className="workbench-heading tasks-heading"><span className="project-page-icon" aria-hidden="true"><Globe2 size={20} /></span><h2>{p.projectName}</h2><span className="project-task-count">{p.count} {t("visible tasks")}</span></header>
      <div className="workbench-views" role="group" aria-label="Task view">{([{ value: "board", label: "Board", Icon: Columns3 }, { value: "list", label: "List", Icon: List }, { value: "table", label: "Table", Icon: Table2 }] as const).map(({ value, label, Icon }) => <button type="button" key={value} aria-pressed={p.viewMode === value} onClick={() => p.setViewMode(value)}><Icon size={14} aria-hidden="true" />{t(label)}</button>)}</div>
    <div className="workbench-toolbar">
      <label className="workbench-search"><Search size={15} aria-hidden="true" /><input aria-label={t("Search tasks")} placeholder={t("Search tasks")} value={p.textFilter} onChange={(e) => p.setTextFilter(e.target.value)} /></label>
      <button className={`workbench-control ${filtersOpen || active.length ? "active" : ""}`} type="button" aria-expanded={filtersOpen} aria-controls="task-filters" onClick={() => setFiltersOpen(!filtersOpen)}><Filter size={14} aria-hidden="true" />{t("Filter")}{active.length ? ` (${active.length})` : ""}</button>
      <label className="workbench-select">{t("Sort")}<select aria-label={t("Sort")} value={p.sort} onChange={(e) => p.setSort(e.target.value as TaskSort)}>{([['activity', 'Activity'], ['title', 'Title'], ['priority', 'Priority'], ['owner', 'Owner'], ['status', 'Status'], ['phase', 'Phase']] as const).map(([value, label]) => <option value={value} key={value}>{t(label)}</option>)}</select></label>
      <button className="workbench-control sort-direction" type="button" aria-label={t(p.descending ? "Descending" : "Ascending")} title={t(p.descending ? "Descending" : "Ascending")} onClick={() => p.setDescending(!p.descending)}><ArrowDownUp size={14} aria-hidden="true" /></button>
      <label className="workbench-select">{t("Group")}<select aria-label={t("Group")} disabled={p.viewMode === "board"} value={p.viewMode === "board" ? "status" : p.group} onChange={(e) => p.setGroup(e.target.value as TaskGroup)}>{([['status', 'Status'], ['owner', 'Owner'], ['priority', 'Priority'], ['none', 'None']] as const).map(([value, label]) => <option value={value} key={value}>{t(label)}</option>)}</select></label>
      {p.viewMode === "table" && <div id="task-view-options" />}
      <span className="toolbar-spacer" />{p.accessControl}
      <button className="workbench-primary" type="button" onClick={p.onNewTask}><Plus size={14} aria-hidden="true" />{t("New task")}</button>
    </div>
    {filtersOpen && <div id="task-filters" className="workbench-filters">
      <div className="task-focus-options" role="group" aria-label="Task focus"><span>{t("Focus")}</span>{TASK_FOCUS_OPTIONS.map((option) => <button type="button" key={option.value} aria-pressed={p.activeFocus === option.value} onClick={() => p.onFocus(option.value)}>{t(option.label)}</button>)}</div>
      <div className="workbench-filter-fields">{filters.map((filter) => <label key={filter.label}>{t(filter.label)}<select aria-label={t(`${filter.label}`)} value={filter.value} onChange={(e) => filter.set(e.target.value)}><option value="all">{t("All")}</option>{filter.options.map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}</select></label>)}</div>
    </div>}
    {(active.length > 0 || p.textFilter) && <div className="workbench-filter-chips">{active.map((filter) => <button type="button" key={filter.label} onClick={() => filter.set("all")}>{t(filter.label)}: {t(filter.options.find(([value]) => value === filter.value)?.[1] || filter.value)}<X size={12} aria-hidden="true" /></button>)}{p.textFilter && <button type="button" onClick={() => p.setTextFilter("")}>{p.textFilter}<X size={12} aria-hidden="true" /></button>}<button type="button" onClick={() => { filters.forEach((filter) => filter.set("all")); p.setTextFilter(""); }}>{t("Clear filters")}</button></div>}
  </>;
}
