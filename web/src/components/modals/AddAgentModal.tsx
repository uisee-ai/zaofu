// AddAgentModal + exclusive closure, extracted verbatim from App.tsx (P1 split).
import type { ChannelSummary } from "../../api/types";
import { Code } from "lucide-react";
import type { AddAgentDraft, ChannelMemberType, ChannelPermissionProfile, ChannelRole, VisibilityProfile } from "../../app/sharedTypes";
import { channelIdOf, channelNameOf, emptyAddAgentDraft } from "../../app/shared";

function defaultVisibilityForChannelRole(role: ChannelRole): VisibilityProfile {
  if (role === "arch" || role === "facilitator" || role === "tech_leader" || role === "product_pm" || role === "synthesizer") return "planner";
  if (role === "owner_delegate" || role === "automation_reporter") return "owner_report";
  if (role === "observer" || role === "researcher") return "minimal";
  return "reviewer";
}


function defaultRoleContextRef(role: ChannelRole): string {
  if (role === "observer") return "";
  return `channel_roles/${role.replaceAll("_", "-")}.md`;
}


export function AddAgentModal({
  actionReady,
  channels,
  draft,
  onChannelChange,
  onClose,
  onDraftChange,
  onSubmit,
  selectedChannelId,
  skillOptions,
}: {
  actionReady: boolean;
  channels: ChannelSummary[];
  draft: AddAgentDraft;
  onChannelChange: (channelId: string) => void;
  onClose: () => void;
  onDraftChange: (draft: AddAgentDraft) => void;
  onSubmit: () => void;
  selectedChannelId: string;
  skillOptions: string[];
}) {
  const update = (patch: Partial<AddAgentDraft>) => onDraftChange({ ...draft, ...patch });
  const channelOptions = channels.length
    ? channels
    : [{ channel_id: "ch-zaofu", name: "# zaofu" } as ChannelSummary];
  const dangerousBlocked = draft.permissionProfile === "dangerous_full" && !draft.dangerousAck;
  const canSubmit = actionReady && Boolean(draft.memberId.trim()) && !dangerousBlocked;
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel" role="dialog" aria-modal="true" aria-label="Add Agent to Channel">
        <div className="section-heading">
          <div>
            <h2>Add Agent</h2>
            <span className="muted">channel member only; execution stays kernel gated</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose}>Close</button>
        </div>
        <div className="modal-body">
          <select className="filter-input" value={selectedChannelId} onChange={(event) => onChannelChange(event.target.value)}>
            {channelOptions.map((channel) => (
              <option key={channelIdOf(channel)} value={channelIdOf(channel)}>{channelNameOf(channel)}</option>
            ))}
          </select>
          <div className="field-row">
            <input
              className="filter-input"
              placeholder="member id, e.g. codex-1"
              value={draft.memberId}
              onChange={(event) => update({ memberId: event.target.value })}
            />
            <select
              className="filter-input"
              value={draft.memberType}
              onChange={(event) => update({ memberType: event.target.value as ChannelMemberType })}
            >
              <option value="provider_agent">Provider agent</option>
              <option value="persona_agent">Persona agent</option>
              <option value="owner_delegate">Owner delegate</option>
              <option value="runtime_role_binding">Runtime role binding</option>
              <option value="observer">Observer</option>
              <option value="automation_reporter">Automation reporter</option>
              <option value="human">Human</option>
            </select>
          </div>
          <div className="field-row">
            <select
              className="filter-input"
              value={draft.provider}
              onChange={(event) => update({ provider: event.target.value, backend: event.target.value })}
            >
              <option value="codex">Codex</option>
              <option value="claude-code">Claude Code</option>
              <option value="hermes">Hermes</option>
              <option value="openclaw">OpenClaw</option>
              <option value="runtime-role">Runtime role</option>
              <option value="fake">Fake/persona</option>
            </select>
            <select
              className="filter-input"
              value={draft.channelRole}
              onChange={(event) => {
                const channelRole = event.target.value as ChannelRole;
                const visibilityProfile = defaultVisibilityForChannelRole(channelRole);
                update({ channelRole, visibilityProfile, roleContextRef: defaultRoleContextRef(channelRole) });
              }}
            >
              <option value="arch">Arch</option>
              <option value="facilitator">Facilitator</option>
              <option value="tech_leader">Tech Leader</option>
              <option value="product_pm">Product PM</option>
              <option value="researcher">Researcher</option>
              <option value="synthesizer">Synthesizer</option>
              <option value="security_reviewer">Security Reviewer</option>
              <option value="qa_analyst">QA Analyst</option>
              <option value="dev_reviewer">Dev Reviewer</option>
              <option value="critic">Critic</option>
              <option value="owner_delegate">Owner Delegate</option>
              <option value="automation_reporter">Automation Reporter</option>
              <option value="observer">Observer</option>
            </select>
          </div>
          <div className="field-row">
            <select
              className="filter-input"
              value={draft.visibilityProfile}
              onChange={(event) => update({ visibilityProfile: event.target.value as VisibilityProfile })}
            >
              <option value="minimal">minimal context</option>
              <option value="planner">planner context</option>
              <option value="reviewer">reviewer context</option>
              <option value="owner_report">owner report context</option>
              <option value="full_audit">full audit context</option>
            </select>
            <input className="filter-input" placeholder="scope" value={draft.scope} onChange={(event) => update({ scope: event.target.value })} />
          </div>
          <div className="field-row">
            <select
              className="filter-input"
              value={draft.permissionProfile}
              onChange={(event) => update({
                permissionProfile: event.target.value as ChannelPermissionProfile,
                dangerousAck: event.target.value === "dangerous_full" ? draft.dangerousAck : false,
              })}
            >
              <option value="read_only">read only</option>
              <option value="artifact_writer">artifact writer</option>
              <option value="project_writer">project writer</option>
              <option value="dangerous_full">dangerous full</option>
            </select>
            {draft.permissionProfile === "dangerous_full" ? (
              <label className="filter-input checkbox-inline">
                <input
                  type="checkbox"
                  checked={draft.dangerousAck}
                  onChange={(event) => update({ dangerousAck: event.target.checked })}
                />
                Confirm dangerous full access
              </label>
            ) : (
              <span className="filter-input muted">writes remain policy-bound</span>
            )}
          </div>
          <input className="filter-input" placeholder="role context ref, e.g. channel_roles/tech-leader.md" value={draft.roleContextRef} onChange={(event) => update({ roleContextRef: event.target.value })} />
          <input
            className="filter-input"
            list="channel-skill-ref-options"
            placeholder="skill refs, e.g. zf-fmea-risk-gate or skills/name/SKILL.md"
            value={draft.skillRefs}
            onChange={(event) => update({ skillRefs: event.target.value })}
          />
          {skillOptions.length ? (
            <datalist id="channel-skill-ref-options">
              {skillOptions.map((option) => (
                <option key={option} value={option} />
              ))}
            </datalist>
          ) : null}
          <input
            className="filter-input"
            placeholder="provider binding id, e.g. remote"
            value={draft.providerBindingId}
            onChange={(event) => update({ providerBindingId: event.target.value })}
          />
          <input className="filter-input" placeholder="reason" value={draft.reason} onChange={(event) => update({ reason: event.target.value })} />
          <div className="checkbox-grid">
            <label><input type="checkbox" checked readOnly /> Read</label>
            <label><input type="checkbox" checked={draft.canMessage} onChange={(event) => update({ canMessage: event.target.checked })} /> Message</label>
            <label><input type="checkbox" checked={draft.canSummarize} onChange={(event) => update({ canSummarize: event.target.checked })} /> Summarize</label>
            <label><input type="checkbox" checked={draft.canProposeWorkflow} onChange={(event) => update({ canProposeWorkflow: event.target.checked })} /> Propose workflow</label>
          </div>
        </div>
        <div className="action-row">
          <button className="icon-button primary" disabled={!canSubmit} type="button" onClick={onSubmit}>
            Add to Channel
          </button>
          <button className="icon-button" type="button" onClick={() => onDraftChange(emptyAddAgentDraft())}>
            Reset
          </button>
        </div>
      </section>
    </div>
  );
}


