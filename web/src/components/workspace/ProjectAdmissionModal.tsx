import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import { useState } from "react";
import { Code2, FolderOpen, KeyRound, LoaderCircle, Search, X } from "lucide-react";

import type { WorkspaceProjectPathInspection } from "../../api/client";
import type { OnboardingBackend } from "../../api/types";

export interface ProjectAdmissionDraft {
  root: string;
  name: string;
  description: string;
  stack: string;
  backend: string;
  mixedEnabled: boolean;
}

export type ProjectAdmissionAccessMode = "passcode" | "token" | "unavailable";

interface Props {
  accessMode: ProjectAdmissionAccessMode;
  actionReady: boolean;
  availableBackends: OnboardingBackend[];
  busy: boolean;
  draft: ProjectAdmissionDraft;
  error: string;
  inspection: WorkspaceProjectPathInspection | null;
  mixedAvailable: boolean;
  onClose: () => void;
  onAuthorize: (credential: string) => Promise<{ ok: boolean; reason?: string }>;
  onDraftChange: (draft: ProjectAdmissionDraft) => void;
  onInspect: () => void;
  onSubmit: () => void;
}

export function ProjectAdmissionModal({
  accessMode,
  actionReady,
  availableBackends,
  busy,
  draft,
  error,
  inspection,
  mixedAvailable,
  onClose,
  onAuthorize,
  onDraftChange,
  onInspect,
  onSubmit,
}: Props) {
  useSettingsPreferences();
  const [authorizationBusy, setAuthorizationBusy] = useState(false);
  const [authorizationError, setAuthorizationError] = useState("");
  const [credential, setCredential] = useState("");
  const root = draft.root.trim();
  const admission = inspection?.admission;
  const initializesProject = admission?.action === "initialize_project";
  const actionable = Boolean(
    actionReady
      && admission
      && admission.action !== "blocked"
      && root
      && (!initializesProject || (draft.name.trim() && draft.backend))
      && !busy,
  );
  const primaryProviders = availableBackends.filter(
    (provider) => provider.id === "codex" || provider.id === "claude-code",
  );
  const providerOptions = primaryProviders.length
    ? primaryProviders
    : [
      { id: "codex", detected: false, path: "", note: "", always_available: false },
      { id: "claude-code", detected: false, path: "", note: "", always_available: false },
    ];
  const detectedLanguages = inspection?.project_profile.languages ?? [];
  const autoStackLabel = detectedLanguages.length
    ? `Auto detect (${detectedLanguages.join(" + ")})`
    : "Auto detect";
  const update = (patch: Partial<ProjectAdmissionDraft>) => {
    onDraftChange({ ...draft, ...patch });
  };
  const authorize = async () => {
    const value = credential.trim();
    if (!value || authorizationBusy) return;
    setAuthorizationBusy(true);
    setAuthorizationError("");
    try {
      const result = await onAuthorize(value);
      if (result.ok) {
        setCredential("");
      } else {
        setAuthorizationError(result.reason || "Authorization failed.");
      }
    } catch (err) {
      setAuthorizationError(err instanceof Error ? err.message : String(err));
    } finally {
      setAuthorizationBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <section
        aria-label={uiText("Add/Open Project")}
        aria-modal="true"
        className="modal-panel project-admission-modal"
        role="dialog"
      >
        <div className="section-heading project-admission-heading">
          <div>
            <h2>{uiText("Add/Open Project")}</h2>
            <span className="muted">default workspace</span>
          </div>
          <button
            aria-label={uiText("Close")}
            className="icon-button"
            title={uiText("Close")}
            type="button"
            onClick={onClose}
          >
            <X aria-hidden="true" size={16} strokeWidth={1.8} />
          </button>
        </div>

        <div className="modal-body project-admission-body">
          <label className="project-admission-path">
            <span>Server project path</span>
            <div className="project-admission-path-row">
              <input
                autoFocus
                className="filter-input"
                data-testid="project-path-input"
                placeholder="/path/to/project"
                value={draft.root}
                onChange={(event) => update({
                  root: event.target.value,
                  name: "",
                  stack: "",
                })}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && root && actionReady && !busy) {
                    onInspect();
                  }
                }}
              />
              <button
                className="icon-button"
                data-testid="project-inspect"
                disabled={!root || !actionReady || busy}
                type="button"
                onClick={onInspect}
              >
                {busy ? (
                  <LoaderCircle aria-hidden="true" className="spin" size={16} />
                ) : (
                  <Search aria-hidden="true" size={16} strokeWidth={1.8} />
                )}
                {uiText("Inspect")}
              </button>
            </div>
          </label>

          {!actionReady ? (
            <div
              className="project-admission-access"
              data-testid="project-admission-access"
              role="status"
            >
              <div className="project-admission-access-label">
                <KeyRound aria-hidden="true" size={16} strokeWidth={1.8} />
                <span>
                  <strong>{uiText("Project access")}</strong>
                  <span className="muted">
                    {accessMode === "passcode"
                      ? "Web session locked"
                      : accessMode === "token"
                        ? "Web action token required"
                        : "Server actions are read only"}
                  </span>
                </span>
              </div>
              {accessMode !== "unavailable" ? (
                <form
                  className="project-admission-access-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void authorize();
                  }}
                >
                  <input
                    aria-label={accessMode === "passcode" ? "Web passcode" : "Web action token"}
                    autoComplete="off"
                    className="filter-input"
                    placeholder={accessMode === "passcode" ? "web passcode" : "action token"}
                    type="password"
                    value={credential}
                    onChange={(event) => setCredential(event.target.value)}
                  />
                  <button
                    className="icon-button"
                    data-testid="project-admission-authorize"
                    disabled={!credential.trim() || authorizationBusy}
                    type="submit"
                  >
                    {authorizationBusy ? (
                      <LoaderCircle aria-hidden="true" className="spin" size={16} />
                    ) : (
                      <KeyRound aria-hidden="true" size={16} strokeWidth={1.8} />
                    )}
                    {accessMode === "passcode" ? "Unlock" : "Authorize"}
                  </button>
                </form>
              ) : null}
              {authorizationError ? (
                <span className="compact-error" role="alert">{authorizationError}</span>
              ) : null}
            </div>
          ) : null}
          {error ? (
            <p className="compact-error" data-testid="project-admission-error" role="alert">
              {error}
            </p>
          ) : null}

          {inspection ? (
            <div
              aria-live="polite"
              className="project-admission-result"
              data-testid="project-admission-result"
            >
              <div className="project-admission-verdict">
                <span
                  className={`badge ${
                    admission?.action === "blocked" ? "badge-err" : "badge-ok"
                  }`}
                  data-testid="project-admission-action"
                >
                  {admission?.action}
                </span>
                <strong>{admission?.label}</strong>
                <span className="muted">{admission?.reason}</span>
              </div>
              <dl className="project-admission-facts">
                <div>
                  <dt>root</dt>
                  <dd>{inspection.root_resolved}</dd>
                </div>
                <div>
                  <dt>config</dt>
                  <dd>{inspection.has_config ? inspection.config_path : "not present"}</dd>
                </div>
                <div>
                  <dt>state</dt>
                  <dd>{inspection.state_dir_resolved}</dd>
                </div>
              </dl>
              {inspection.diagnostics.length ? (
                <ul className="project-admission-diagnostics">
                  {inspection.diagnostics.map((diagnostic, index) => (
                    <li key={`${diagnostic.kind}-${index}`}>
                      <span className="mono">{diagnostic.severity}</span>
                      <span>{diagnostic.message}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {initializesProject ? (
                <div
                  className="project-admission-metadata"
                  data-testid="project-admission-metadata"
                >
                  <div className="project-admission-field-grid">
                    <label>
                      <span>{uiText("Project name")}</span>
                      <input
                        className="filter-input"
                        data-testid="project-name-input"
                        maxLength={80}
                        value={draft.name}
                        onChange={(event) => update({ name: event.target.value })}
                      />
                    </label>
                    <label>
                      <span>{uiText("Project stack")}</span>
                      <select
                        className="filter-input"
                        data-testid="project-stack-select"
                        value={draft.stack}
                        onChange={(event) => update({ stack: event.target.value })}
                      >
                        <option value="">{autoStackLabel}</option>
                        <option value="python">Python</option>
                        <option value="node">Node.js</option>
                        <option value="go">Go</option>
                        <option value="rust">Rust</option>
                      </select>
                    </label>
                    <label className="project-admission-description">
                      <span>{uiText("Project brief")} <span className="muted">optional</span></span>
                      <textarea
                        className="filter-input"
                        data-testid="project-description-input"
                        maxLength={2000}
                        placeholder="Business context, target users, scope, and current stage"
                        rows={3}
                        value={draft.description}
                        onChange={(event) => update({ description: event.target.value })}
                      />
                    </label>
                  </div>
                  <fieldset className="project-provider-policy">
                    <legend>{uiText("Primary provider")}</legend>
                    <div className="project-provider-options">
                      {providerOptions.map((provider) => (
                        <button
                          aria-pressed={draft.backend === provider.id}
                          className={`project-provider-option ${
                            draft.backend === provider.id ? "is-selected" : ""
                          }`}
                          data-testid={`project-provider-${provider.id}`}
                          key={provider.id}
                          type="button"
                          onClick={() => update({ backend: provider.id })}
                        >
                          <Code2 aria-hidden="true" size={15} strokeWidth={1.8} />
                          <span>{provider.id}</span>
                          <span className={provider.detected ? "provider-ready" : "muted"}>
                            {provider.detected ? "detected" : "not detected"}
                          </span>
                        </button>
                      ))}
                    </div>
                  </fieldset>
                  <label className="project-mixed-policy">
                    <input
                      checked={draft.mixedEnabled}
                      data-testid="project-mixed-enabled"
                      disabled={!mixedAvailable}
                      type="checkbox"
                      onChange={(event) => update({ mixedEnabled: event.target.checked })}
                    />
                    <span>
                      <strong>{uiText("Mixed team")}</strong>
                      <span className="muted">
                        {mixedAvailable
                          ? "Independent verify lanes use the other provider."
                          : "Requires Codex and Claude Code on this host."}
                      </span>
                    </span>
                  </label>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="project-admission-empty" data-testid="project-admission-empty">
              <FolderOpen aria-hidden="true" size={18} strokeWidth={1.6} />
              <span className="muted">{uiText("No path inspected")}</span>
            </div>
          )}
        </div>

        <div className="action-row project-admission-actions">
          <button className="icon-button" type="button" onClick={onClose}>
            {uiText("Cancel")}
          </button>
          {admission && admission.action !== "blocked" ? (
            <button
              className="primary-action"
              data-testid="project-admission-submit"
              disabled={!actionable}
              type="button"
              onClick={onSubmit}
            >
              {busy ? (
                <LoaderCircle aria-hidden="true" className="spin" size={16} />
              ) : (
                <FolderOpen aria-hidden="true" size={16} strokeWidth={1.8} />
              )}
              {admission.label}
            </button>
          ) : null}
        </div>
      </section>
    </div>
  );
}
