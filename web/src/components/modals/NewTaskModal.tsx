// NewTaskModal + exclusive closure, extracted verbatim from App.tsx (P1 split).
import type { AgentSummary, ChannelSummary, Task } from "../../api/types";
import type { NewTaskAssigneeType, NewTaskDraft } from "../../app/sharedTypes";
import { channelIdOf, channelNameOf, emptyNewTaskDraft } from "../../app/shared";

export function NewTaskModal({
  actionReady,
  agents,
  channels,
  draft,
  hasActiveProject,
  onClose,
  onDraftChange,
  onSubmit,
  projectLabel,
}: {
  actionReady: boolean;
  agents: AgentSummary[];
  channels: ChannelSummary[];
  draft: NewTaskDraft;
  hasActiveProject: boolean;
  onClose: () => void;
  onDraftChange: (draft: NewTaskDraft) => void;
  onSubmit: () => void;
  projectLabel: string;
}) {
  const update = (patch: Partial<NewTaskDraft>) => onDraftChange({ ...draft, ...patch });
  const agentOptions = agents.filter((agent) =>
    agent.instance_id && !["control", "web_surface"].includes(agent.agent_kind)
  );
  const squadOptions = channels.filter((channel) => channelIdOf(channel));
  const assigneeType = draft.assigneeType || "none";
  const setAssigneeType = (next: NewTaskAssigneeType) => {
    update({
      assigneeType: next,
      assigneeId: "",
      assigneeLabel: "",
      assigneeBackend: "",
      assigneeSupervisor: "",
      assignedTo: "",
    });
  };
  const selectAgent = (instanceId: string) => {
    const agent = agentOptions.find((item) => item.instance_id === instanceId);
    update({
      assigneeType: "agent",
      assigneeId: instanceId,
      assigneeLabel: agent?.instance_id || instanceId,
      assigneeBackend: agent?.backend || "",
      assigneeSupervisor: "",
      assignedTo: instanceId,
    });
  };
  const selectSquad = (channelId: string) => {
    const channel = squadOptions.find((item) => channelIdOf(item) === channelId);
    update({
      assigneeType: "squad",
      assigneeId: channelId,
      assigneeLabel: channel ? channelNameOf(channel) : channelId,
      assigneeBackend: "",
      assignedTo: "",
    });
  };
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel" role="dialog" aria-modal="true" aria-label="New Task">
        <div className="section-heading">
          <div>
            <h2>New Task</h2>
            <span className="muted">draft stays local until create-task</span>
          </div>
          <span className={`metric-chip ${hasActiveProject ? "" : "chip-warn"}`}>
            Project {projectLabel}
          </span>
          <button className="icon-button" type="button" onClick={onClose}>Close</button>
        </div>
        <div className="modal-body">
          <input className="filter-input" placeholder="title" value={draft.title} onChange={(event) => update({ title: event.target.value })} />
          <textarea className="textarea-input" placeholder="behavior" value={draft.behavior} onChange={(event) => update({ behavior: event.target.value })} />
          <textarea className="textarea-input" placeholder="verification" value={draft.verification} onChange={(event) => update({ verification: event.target.value })} />
          <div className="field-row">
            <select
              className="filter-input"
              value={assigneeType}
              onChange={(event) => setAssigneeType(event.target.value as NewTaskAssigneeType)}
            >
              <option value="none">Unassigned</option>
              <option value="agent">Agent</option>
              <option value="squad">Squad</option>
            </select>
            <select className="filter-input" value={draft.priority} onChange={(event) => update({ priority: event.target.value })}>
              {[0, 1, 2, 3, 4, 5].map((priority) => <option key={priority} value={String(priority)}>P{priority}</option>)}
            </select>
          </div>
          {assigneeType === "agent" ? (
            <div className="field-row">
              <select
                className="filter-input"
                value={draft.assigneeId}
                onChange={(event) => selectAgent(event.target.value)}
              >
                <option value="">Choose agent</option>
                {agentOptions.map((agent) => (
                  <option key={agent.instance_id} value={agent.instance_id}>
                    {agent.instance_id} · {agent.backend || agent.agent_kind}
                  </option>
                ))}
              </select>
              <input
                className="filter-input"
                placeholder="supervisor optional"
                value={draft.assigneeSupervisor}
                onChange={(event) => update({ assigneeSupervisor: event.target.value })}
              />
            </div>
          ) : null}
          {assigneeType === "squad" ? (
            <div className="field-row">
              <select
                className="filter-input"
                value={draft.assigneeId}
                onChange={(event) => selectSquad(event.target.value)}
              >
                <option value="">Choose squad</option>
                {squadOptions.map((channel) => (
                  <option key={channelIdOf(channel)} value={channelIdOf(channel)}>
                    {channelNameOf(channel)}
                  </option>
                ))}
              </select>
              <input
                className="filter-input"
                placeholder="leader/supervisor optional"
                value={draft.assigneeSupervisor}
                onChange={(event) => update({ assigneeSupervisor: event.target.value })}
              />
            </div>
          ) : null}
          <div className="muted small">
            Assignee is an intent; dispatch remains controlled by Project actions.
          </div>
          <input className="filter-input" placeholder="skills, comma separated" value={draft.skills} onChange={(event) => update({ skills: event.target.value })} />
          <input className="filter-input" placeholder="blocked_by task ids, comma separated" value={draft.blockedBy} onChange={(event) => update({ blockedBy: event.target.value })} />
        </div>
        <div className="action-row">
          <button className="icon-button primary" disabled={!hasActiveProject || !actionReady || !draft.title.trim()} type="button" onClick={onSubmit}>
            Create Task
          </button>
          <button className="icon-button" type="button" onClick={() => onDraftChange(emptyNewTaskDraft())}>
            Clear Draft
          </button>
        </div>
      </section>
    </div>
  );
}


