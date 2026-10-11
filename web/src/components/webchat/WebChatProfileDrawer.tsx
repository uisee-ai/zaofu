import { Check, Settings2, ShieldCheck, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { applyAgentProfile, getAgentProfileCatalog, proposeAgentProfile } from "../../api/client";
import type { AgentProfileCatalog } from "../../api/types";
import { useWebChatDrawer } from "./useWebChatDrawer";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function WebChatProfileDrawer({
  currentPermission,
  currentProvider,
  onClose,
  onApplied,
  projectId,
}: {
  currentPermission: string;
  currentProvider: string;
  onClose: () => void;
  onApplied: () => Promise<void> | void;
  projectId: string;
}) {
  const drawerRef = useWebChatDrawer(onClose);
  const [catalog, setCatalog] = useState<AgentProfileCatalog | null>(null);
  const [provider, setProvider] = useState(currentProvider);
  const [permission, setPermission] = useState(currentPermission);
  const [role, setRole] = useState("operator");
  const [skills, setSkills] = useState<string[]>([]);
  const [tools, setTools] = useState<string[]>(["task.read", "workflow.read", "artifact.read"]);
  const [proposal, setProposal] = useState<Record<string, unknown> | null>(null);
  const [diff, setDiff] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const validation = record(proposal?.validation);
  const validationErrors = Array.isArray(validation.errors)
    ? validation.errors.flatMap((item) => {
      const row = record(item);
      const field = String(row.field || "profile");
      const reason = String(row.reason || "invalid value");
      return [`${field}: ${reason}`];
    })
    : [];
  const proposalValid = proposal !== null && validation.valid !== false;

  useEffect(() => {
    void getAgentProfileCatalog(projectId).then((next) => {
      setCatalog(next);
      const current = record(next.current_profile);
      if (!Object.keys(current).length) return;
      setProvider(String(current.provider || currentProvider));
      setPermission(String(current.permission_profile || currentPermission));
      setRole(String(current.role || "operator"));
      setSkills(Array.isArray(current.skills) ? current.skills.map(String) : []);
      setTools(Array.isArray(current.tools) ? current.tools.map(String) : []);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [projectId]);

  const baseProfile = useMemo(() => {
    const current = record(catalog?.current_profile);
    return Object.keys(current).length ? current : {
      role: "operator",
      provider: currentProvider,
      permission_profile: currentPermission,
      skills: [],
      tools: ["task.read", "workflow.read", "artifact.read"],
      eligibility: { ephemeral: true, channel: false, delivery: false },
    };
  }, [catalog?.current_profile, currentPermission, currentProvider]);
  const nextProfile = {
    ...baseProfile,
    role,
    provider,
    permission_profile: permission,
    skills,
    tools,
    eligibility: { ephemeral: true, channel: role === "channel", delivery: role === "delivery" },
    artifact_policy: { private_by_default: true, publish_requires_owner: true },
  };

  function toggle(items: string[], value: string, update: (next: string[]) => void) {
    update(items.includes(value) ? items.filter((item) => item !== value) : [...items, value]);
  }

  async function propose() {
    setBusy("propose");
    setError("");
    try {
      const response = await proposeAgentProfile(projectId, "kanban-agent", {
        base_profile: baseProfile,
        profile: nextProfile,
        permission_ceiling: "workspace_writer",
      });
      const item = record(response.proposal);
      setProposal(item);
      setDiff(String(item.diff || ""));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  async function apply() {
    if (!proposal) return;
    setBusy("apply");
    setError("");
    try {
      await applyAgentProfile(projectId, String(proposal.proposal_id || ""), {
        base_digest: proposal.base_digest,
        proposal_digest: proposal.proposal_digest,
      });
      await onApplied();
      setProposal(null);
      setDiff("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  return (
    <aside aria-label="Agent profile" aria-modal="true" className="webchat-drawer" ref={drawerRef} role="dialog" tabIndex={-1}>
      <header className="webchat-drawer-header">
        <span><Settings2 size={16} /> Agent profile</span>
        <button aria-label="Close agent profile" className="agent-window-button ghost" type="button" onClick={onClose}><X size={16} /></button>
      </header>
      <div className="webchat-profile-form">
        <label>Provider<select value={provider} onChange={(event) => setProvider(event.target.value)}>
          {(catalog?.providers || [currentProvider]).map((item) => <option key={item}>{item}</option>)}
        </select></label>
        <label>Role<select value={role} onChange={(event) => setRole(event.target.value)}>
          {(catalog?.roles || ["operator"]).map((item) => <option key={item}>{item}</option>)}
        </select></label>
        <label>Permission<select value={permission} onChange={(event) => setPermission(event.target.value)}>
          {(catalog?.permission_profiles || [currentPermission]).map((item) => <option key={item}>{item}</option>)}
        </select></label>
        <fieldset>
          <legend>Skills</legend>
          <div className="webchat-profile-options">
            {(catalog?.skills || []).map((item) => (
              <label key={item}><input checked={skills.includes(item)} type="checkbox" onChange={() => toggle(skills, item, setSkills)} />{item}</label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>Tools</legend>
          <div className="webchat-profile-options">
            {(catalog?.tools || []).map((item) => (
              <label key={item}><input checked={tools.includes(item)} type="checkbox" onChange={() => toggle(tools, item, setTools)} />{item}</label>
            ))}
          </div>
        </fieldset>
        <p className="webchat-profile-boundary"><ShieldCheck size={15} /> Proposal only. No secret or direct <code>zf.yaml</code> write.</p>
        {error ? <p className="webchat-resource-error" role="alert">{error}</p> : null}
        {proposal && !proposalValid ? (
          <div className="webchat-resource-error" role="alert">
            <strong>Proposal rejected</strong>
            {validationErrors.map((item) => <div key={item}>{item}</div>)}
          </div>
        ) : null}
        {diff ? <pre className="webchat-profile-diff">{diff}</pre> : null}
        <div className="webchat-profile-actions">
          <button disabled={Boolean(busy)} type="button" onClick={() => void propose()}><Settings2 size={14} /> Preview proposal</button>
          {proposal ? <button disabled={Boolean(busy) || !proposalValid} type="button" onClick={() => void apply()}><Check size={14} /> Controlled apply</button> : null}
        </div>
      </div>
    </aside>
  );
}
