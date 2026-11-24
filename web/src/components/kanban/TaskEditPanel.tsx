import { useEffect, useState } from "react";
import type { Task, TaskDetail as TaskDetailModel } from "../../api/types";
import { csvList, recordValue, textValue } from "../../app/shared";
import { taskPriority } from "../../lib/task-display";

interface TaskEditDraft {
  title: string;
  priority: string;
  blockedReason: string;
  blockedBy: string;
  skills: string;
  behavior: string;
  verification: string;
  scope: string;
  exclusions: string;
  acceptance: string;
  reworkTo: string;
}


function joinCsv(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean).join(", ") : "";
}


function taskEditDraft(task: Task, detail: TaskDetailModel | null): TaskEditDraft {
  const taskRecord = task as Task & Record<string, unknown>;
  const contract = recordValue(detail?.contract) ?? recordValue(taskRecord.contract) ?? {};
  return {
    title: task.title || "",
    priority: String(taskPriority(task)),
    blockedReason: task.blocked_reason || "",
    blockedBy: joinCsv(task.blocked_by),
    skills: joinCsv(task.skills_required),
    behavior: textValue(contract.behavior),
    verification: textValue(contract.verification),
    scope: joinCsv(contract.scope),
    exclusions: joinCsv(contract.exclusions),
    acceptance: textValue(contract.acceptance || "exit_code=0"),
    reworkTo: textValue(contract.rework_to),
  };
}

export function TaskEditPanel({
  actionReady,
  actionState,
  detail,
  onUpdate,
  task,
}: {
  actionReady: boolean;
  actionState: string;
  detail: TaskDetailModel | null;
  onUpdate: (payload: Record<string, unknown>) => void;
  task: Task;
}) {
  const [edits, setEdits] = useState<Partial<TaskEditDraft>>({});
  const current = taskEditDraft(task, detail);
  const draft = { ...current, ...edits };
  const dirty = JSON.stringify(draft) !== JSON.stringify(current);
  useEffect(() => {
    const stored = taskEditDraft(task, detail);
    setEdits(previous => {
      const keys = Object.keys(previous) as Array<keyof TaskEditDraft>;
      const saved = keys.filter(key => previous[key] === stored[key]);
      if (!saved.length) return previous;
      const remaining = { ...previous };
      for (const key of saved) delete remaining[key];
      return remaining;
    });
  }, [task, detail]);

  const update = (patch: Partial<TaskEditDraft>) => setEdits(previous => ({ ...previous, ...patch }));

  function saveTaskEdit() {
    onUpdate({
      title: draft.title,
      priority: Number(draft.priority || 3),
      blocked_reason: draft.blockedReason,
      blocked_by: csvList(draft.blockedBy),
      skills_required: csvList(draft.skills),
      contract: {
        behavior: draft.behavior,
        verification: draft.verification,
        scope: csvList(draft.scope),
        exclusions: csvList(draft.exclusions),
        acceptance: draft.acceptance || "exit_code=0",
        rework_to: draft.reworkTo,
      },
    });
  }

  return (
    <div className="subsection task-edit-panel">
      <div className="inline-heading">
        <h3>Maintenance</h3>
        <span className="muted">{actionReady ? "controlled action" : actionState}</span>
      </div>
      <div className="task-edit-grid">
        <label>
          <span>Title</span>
          <input className="filter-input" value={draft.title} onChange={(event) => update({ title: event.target.value })} />
        </label>
        <label>
          <span>Priority</span>
          <select className="filter-input" value={draft.priority} onChange={(event) => update({ priority: event.target.value })}>
            {[0, 1, 2, 3, 4, 5].map((priority) => <option key={priority} value={String(priority)}>P{priority}</option>)}
          </select>
        </label>
        <label>
          <span>Skills</span>
          <input className="filter-input" value={draft.skills} onChange={(event) => update({ skills: event.target.value })} />
        </label>
        <label>
          <span>Blocked By</span>
          <input className="filter-input" value={draft.blockedBy} onChange={(event) => update({ blockedBy: event.target.value })} />
        </label>
        <label className="wide-field">
          <span>Blocked Reason</span>
          <input className="filter-input" value={draft.blockedReason} onChange={(event) => update({ blockedReason: event.target.value })} />
        </label>
        <label className="wide-field">
          <span>Behavior</span>
          <textarea className="textarea-input compact-textarea" value={draft.behavior} onChange={(event) => update({ behavior: event.target.value })} />
        </label>
        <label className="wide-field">
          <span>Verification</span>
          <textarea className="textarea-input compact-textarea" value={draft.verification} onChange={(event) => update({ verification: event.target.value })} />
        </label>
        <label>
          <span>Scope</span>
          <input className="filter-input" value={draft.scope} onChange={(event) => update({ scope: event.target.value })} />
        </label>
        <label>
          <span>Exclusions</span>
          <input className="filter-input" value={draft.exclusions} onChange={(event) => update({ exclusions: event.target.value })} />
        </label>
        <label>
          <span>Acceptance</span>
          <input className="filter-input" value={draft.acceptance} onChange={(event) => update({ acceptance: event.target.value })} />
        </label>
        <label>
          <span>Rework To</span>
          <input className="filter-input" value={draft.reworkTo} onChange={(event) => update({ reworkTo: event.target.value })} />
        </label>
      </div>
      <div className="action-row flush-action-row">
        <button className="icon-button primary" disabled={!actionReady || !dirty} type="button" onClick={saveTaskEdit}>
          Save Task
        </button>
        <button className="icon-button" disabled={!dirty} type="button" onClick={() => setEdits({})}>
          Reset
        </button>
      </div>
    </div>
  );
}
