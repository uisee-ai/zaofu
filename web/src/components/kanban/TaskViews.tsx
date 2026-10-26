import { ChevronDown, ChevronRight, Columns3 } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { Task } from "../../api/types";
import { useUiPreferences } from "../../app/UiPreferences";
import { formatDateTime } from "../../lib/uiPreferences";
import { taskPriority, taskRiskBadge } from "../../lib/task-display";
import type { TaskTelemetry } from "../../lib/task-display";
import { taskColumn } from "./board";
import { groupTasks, taskActivity, taskOwner, taskPhase, taskStatus } from "./taskViewModel";
import type { TaskGroup, TaskSort } from "./taskViewModel";

interface TaskViewsProps {
  tasks: Task[]; view: "list" | "table"; group: TaskGroup; selectedTaskId: string | null;
  telemetry: Map<string, TaskTelemetry>; onOpenTask: (id: string) => void;
  sort: TaskSort; descending: boolean; onSort: (sort: TaskSort) => void;
}
const COLUMNS = [
  { id: "title", label: "Title", width: 340, sort: "title" }, { id: "status", label: "Status", width: 130, sort: "status" },
  { id: "owner", label: "Owner", width: 160, sort: "owner" }, { id: "priority", label: "Priority", width: 90, sort: "priority" },
  { id: "phase", label: "Phase", width: 135, sort: "phase" }, { id: "activity", label: "Activity", width: 200, sort: "activity" },
  { id: "skills", label: "Skills", width: 170 }, { id: "dependencies", label: "Dependencies", width: 160 },
  { id: "evidence", label: "Evidence", width: 180 }, { id: "retries", label: "Retries", width: 85 }, { id: "cost", label: "Cost", width: 100 },
] as const;
type ColumnId = typeof COLUMNS[number]["id"];
const TABLE_KEY = "zf.taskTable.v1";
function readTablePreferences(): { columns: ColumnId[]; widths: Record<string, number> } {
  try {
    const data = JSON.parse(localStorage.getItem(TABLE_KEY) || "null");
    if (!data || !Array.isArray(data.columns)) throw new Error("missing");
    return { columns: ["title", ...COLUMNS.filter((c) => c.id !== "title" && data.columns.includes(c.id)).map((c) => c.id)], widths: Object.fromEntries(COLUMNS.map((c) => [c.id, Math.max(80, Math.min(600, Number(data.widths?.[c.id]) || c.width))])) };
  } catch { return { columns: COLUMNS.slice(0, 6).map((c) => c.id), widths: {} }; }
}
export function TaskViews(p: TaskViewsProps) {
  const { t, preferences } = useUiPreferences();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [table, setTable] = useState(readTablePreferences);
  const [columnPicker, setColumnPicker] = useState(false);
  const [toolsHost, setToolsHost] = useState<HTMLElement | null>(null);
  useEffect(() => { setToolsHost(document.getElementById("task-view-options")); }, [p.view]);
  const groups = groupTasks(p.tasks, p.group);
  const columns = COLUMNS.filter((c) => table.columns.includes(c.id));
  function saveTable(next: typeof table) {
    setTable(next);
    try { localStorage.setItem(TABLE_KEY, JSON.stringify(next)); } catch { /* layout stays usable without persistence */ }
  }
  function toggle(id: string) { setCollapsed((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; }); }
  function groupButton(group: typeof groups[number]) {
    const id = `${p.group}:${group.id}`;
    return <button type="button" className="task-group-toggle" aria-expanded={!collapsed.has(id)} onClick={() => toggle(id)}>{collapsed.has(id) ? <ChevronRight size={14} /> : <ChevronDown size={14} />}<span className={`task-status-dot status-${group.id}`} />{t(group.title)}<span className="muted">{group.tasks.length}</span></button>;
  }
  function titleCell(task: Task) {
    const risk = taskRiskBadge(task, p.telemetry.get(task.id));
    return <><span className="task-row-title">{task.title || task.id}</span>{risk.label && <span className={`task-row-risk badge-${risk.tone}`} title={risk.label} aria-label={risk.label}>!</span>}<span className="task-row-id mono">{task.id}</span></>;
  }
  function owner(task: Task) {
    const name = taskOwner(task);
    return <span className="task-owner"><span className="task-assignee-avatar assigned" aria-hidden="true">{name === "Unassigned" ? "–" : name.replace(/^@/, "").split(/[-_ /]+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase()}</span><span>{t(name)}</span></span>;
  }
  function cell(task: Task, id: ColumnId) {
    if (id === "title") return <button type="button" className="task-table-open" onClick={() => p.onOpenTask(task.id)}>{titleCell(task)}</button>;
    if (id === "status") return <span className={`task-status-label status-${taskColumn(task)}`}><i className={`task-status-dot status-${taskColumn(task)}`} />{t(taskStatus(task))}</span>;
    if (id === "owner") return owner(task);
    if (id === "priority") return `P${taskPriority(task)}`;
    if (id === "phase") return taskPhase(task);
    if (id === "activity") return <time dateTime={taskActivity(task)}>{formatDateTime(taskActivity(task), preferences) || "—"}</time>;
    if (id === "skills") return task.skills_required?.join(", ") || "—";
    if (id === "dependencies") return task.blocked_by?.join(", ") || "—";
    if (id === "evidence") return task.evidence_badges?.map((badge) => badge.label).join(", ") || "—";
    if (id === "retries") return task.retry_count ?? 0;
    const cost = p.telemetry.get(task.id)?.usd;
    return cost == null ? "—" : new Intl.NumberFormat("en", { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(cost);
  }
  if (!p.tasks.length) return <div className="workbench-empty"><h3>{t("No matching tasks")}</h3><p>{t("Try changing your search or filters.")}</p></div>;
  if (p.view === "list") return <div className="workbench-task-list">
    <div className="task-list-columns" aria-hidden="true"><span>{t("Title")}</span><span>{t("Status")}</span><span>{t("Owner")}</span><span>{t("Priority")}</span><span>{t("Activity")}</span><span>{t("Skills")}</span></div>
    {groups.map((group) => <section key={group.id} className="task-row-group">{p.group !== "none" && groupButton(group)}{!collapsed.has(`${p.group}:${group.id}`) && <div role="list">{group.tasks.map((task) => <div role="listitem" key={task.id}><button type="button" className={`workbench-task-row ${p.selectedTaskId === task.id ? "selected" : ""}`} onClick={() => p.onOpenTask(task.id)} data-task-id={task.id}><span className="task-list-title"><span className={`task-status-dot status-${taskColumn(task)}`} aria-label={taskStatus(task)} />{titleCell(task)}</span><span className="task-row-status">{cell(task, "status")}</span><span className="task-row-owner" title={taskOwner(task)}>{owner(task)}</span><span className="task-row-priority">P{taskPriority(task)}</span><time className="task-row-activity" dateTime={taskActivity(task)} title={formatDateTime(taskActivity(task), preferences)}>{formatDateTime(taskActivity(task), preferences, true) || "—"}</time><span className="task-row-labels">{task.skills_required?.slice(0, 2).map((skill) => <span className="task-label" key={skill}><i aria-hidden="true" />{skill}</span>)}</span></button></div>)}</div>}</section>)}
  </div>;
  return <div className="workbench-table-view">
    {toolsHost && createPortal(<div className="table-options"><button className="workbench-control" type="button" aria-expanded={columnPicker} onClick={() => setColumnPicker(!columnPicker)}><Columns3 size={14} aria-hidden="true" />{t("Columns")}</button>{columnPicker && <fieldset className="table-column-picker"><legend>{t("Columns")}</legend>{COLUMNS.map((column) => <label key={column.id}><input type="checkbox" checked={table.columns.includes(column.id)} disabled={column.id === "title"} onChange={(event) => saveTable({ ...table, columns: event.target.checked ? [...table.columns, column.id] : table.columns.filter((id) => id !== column.id) })} />{t(column.label)}</label>)}</fieldset>}</div>, toolsHost)}
    <div className="workbench-table-scroll"><table className="workbench-task-table" style={{ width: columns.reduce((sum, c) => sum + (table.widths[c.id] || c.width), 0) }}><colgroup>{columns.map((c) => <col key={c.id} style={{ width: table.widths[c.id] || c.width }} />)}</colgroup><thead><tr>{columns.map((column) => <th key={column.id} scope="col" aria-sort={"sort" in column && p.sort === column.sort ? p.descending ? "descending" : "ascending" : undefined}>
      {"sort" in column ? <button type="button" onClick={() => p.onSort(column.sort)}>{t(column.label)}{p.sort === column.sort ? p.descending ? " ↓" : " ↑" : ""}</button> : t(column.label)}
      <input className="table-column-width" aria-label={`${t(column.label)} width`} title={`${t(column.label)} width`} type="range" min={80} max={600} step={10} value={table.widths[column.id] || column.width} onChange={(event) => saveTable({ ...table, widths: { ...table.widths, [column.id]: Number(event.target.value) } })} />
    </th>)}</tr></thead><tbody>{groups.map((group) => <Fragment key={group.id}>{p.group !== "none" && <tr className="table-group-row"><td colSpan={columns.length}>{groupButton(group)}</td></tr>}{!collapsed.has(`${p.group}:${group.id}`) && group.tasks.map((task) => <tr key={task.id} data-task-id={task.id} className={p.selectedTaskId === task.id ? "selected" : ""}>{columns.map((column) => <td key={column.id}>{cell(task, column.id)}</td>)}</tr>)}</Fragment>)}</tbody></table></div>
  </div>;
}
