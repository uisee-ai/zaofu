import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { OrchestratorPanel } from "../../../src/components/orchestrator/OrchestratorPanel";
import type { Snapshot } from "../../../src/api/types";

const calls: Array<{ action: string; payload: Record<string, unknown> }> = [];
const baseDraft = {
  draft_id: "sid-fixture-1", revision: 1, schema_version: "issue-draft.v2",
  title: "Page freezes after Save", summary: "The page freezes after Save.",
  bug_description: "The page freezes after Save.", reproduction_steps: "1. Open the page\n2. Click Save",
  actual_behavior: "The page stops responding.", expected_behavior: "The page should save and remain responsive.",
  attachment_context: "", additional_context: "", environment: { os: "Linux", version: "24.04", arch: "x86_64", source: "host_detector" },
  project_version: "4.5.6", zaofu_version: "4.5.6", classification: "unknown", severity: "P2",
  reproduction_status: "unverified", component: "unknown", impact_scope: "unknown", assessment_confidence: "low",
  recommended_next_action: "", analysis: {}, evidence_refs: [], attachment_refs: [], published_attachments: [],
  runtime_status: "live", evidence_status: "pending", evidence_collection_mode: "pending",
  assessment_status: "not_started", publication_state: "draft", target_binding: { provider: "github", project: "owner/repo" },
  enrichment: { evaluation_locked: false, evaluation_runs: [] },
  target_policy: { locked: false, allowed_modes: ["github"], default_mode: "github", targets: {} },
};

function snapshot(): Snapshot {
  return {
    seq: 1, generated_at: new Date().toISOString(),
    project: { project_id: "fixture", name: "fixture", root: "/fixture", state_dir: ".state" },
    tasks: [], archive_tasks: [], features: [], traces: [], fanouts: [], candidates: [], runs: [], active_runs: [],
    roles: [], workdirs: [], skills: { items: [] } as never, cost: {} as never, workers: [],
    runtime: { live: true, actions: { mutation_enabled: true, allowed: ["chat-orchestrator", "self-issue-capture", "self-issue-update", "self-issue-evaluation-start", "self-issue-evaluation-cancel", "self-issue-limited-continue", "self-issue-preview", "self-issue-get", "self-issue-publish", "self-issue-confirm", "self-issue-dismiss", "self-issue-suggestion-dismiss", "self-issue-suggestion-resolve", "self-issue-conflict-resolve"] }, agent_surface: {
      id: "fixture-agent", session_id: "fixture-session", status: "ready", scope: "project", backend: "claude-headless",
      configured_backend: "claude-headless", default_backend: "claude-headless", profile: "operator", terminal_backed: false,
      delivery: "fixture", capabilities: ["chat"], allowed_actions: ["chat-orchestrator"], forbidden: [], last_event_seq: 0,
      backends: [{ id: "claude-headless", title: "Claude", available: true, default: true }],
    } } as never,
  } as Snapshot;
}

function response(action: string, payload: Record<string, unknown>): Record<string, unknown> {
  if (action === "self-issue-capture") {
    const description = String(payload.description || "");
    if (description.includes("点击保存后")) {
      return {
        ok: true,
        status: "draft_ready",
        draft: {
          ...baseDraft,
          title: "保存后页面持续卡死且刷新无效",
          summary: "设置页点击保存后界面持续卡死，刷新后仍无法恢复。",
          bug_description: "在设置页修改通知选项并保存后，页面一直显示处理中，其他操作也没有响应。",
          reproduction_steps: "1. 打开设置页\n2. 修改通知选项\n3. 点击保存\n4. 刷新页面",
          actual_behavior: "页面持续显示处理中，刷新后仍卡死。",
          expected_behavior: "保存完成后页面恢复可操作，并显示最新设置。",
          attachment_context: "用户提到控制台截图，但本次未附加文件。",
          description,
        },
        start_evidence: false,
      };
    }
    const scenario = new URLSearchParams(window.location.search).get("scenario");
    if (scenario === "portable") return {
      ok: true, session_id: "issue-session-composer", session_status: "ready_for_confirmation",
      report: { title: "Issue with attachments", actual_behavior: description },
      workflow: { phase: "ready_for_confirmation", activity_log: [] },
      working_set: { evidences: payload.attachments || [] },
    };
    const locked = new URLSearchParams(window.location.search).get("locked") === "1";
    const enrichment = scenario === "enrichment" ? {
      schema_version: "issue-enrichment-projection.v1",
      draft_id: baseDraft.draft_id,
      draft_revision: 1,
      draft_digest: `sha256:${"a".repeat(64)}`,
      field_metadata: {}, revisions: [], evidences: [], gaps: [], resolutions: [], dismissals: [],
      evaluation_locked: locked,
      evaluation_runs: locked
        ? [{ run_id: "evaluation-fixture", status: "running", draft_revision: 1 }]
        : [],
      claims: [
        { claim_id: "claim-reported", value: "Page remains responsive." },
        { claim_id: "claim-observed", value: "Page becomes unresponsive." },
      ],
      suggestions: [{
        suggestion_id: "suggestion-fixture", status: "pending", field_path: "actual_behavior",
        current_value: "The page stops responding.", proposed_value: "The Save request remains pending.",
        reason: "Observed in the read-only reproduction.", confidence: "high", evidence_refs: ["evidence-1"],
      }],
      conflicts: [{
        conflict_id: "conflict-fixture", status: "unresolved", field_path: "expected_behavior",
        left_claim_ref: "claim-reported", right_claim_ref: "claim-observed",
        reason: "The report and observed behavior differ.",
      }],
    } : baseDraft.enrichment;
    return {
      ok: true,
      status: "draft_ready",
      draft: { ...baseDraft, description, enrichment },
      start_evidence: false,
    };
  }
  if (action === "self-issue-draft-attachment-add") return {
    ok: true,
    status: "draft_attachment_added",
    draft: {
      ...baseDraft,
      revision: 2,
      attachment_refs: [{
        attachment_id: "att-fixture-1", filename: String(payload.filename || "runtime.log"),
        byte_count: 13, content_type: "text/plain", sha256: "a".repeat(64),
        ref: "artifacts/self-issues/sid-fixture-1/attachments/att-fixture-1-runtime.log",
      }],
    },
  };
  if (action === "self-issue-draft-attachment-remove") return {
    ok: true, status: "draft_attachment_removed", draft: { ...baseDraft, revision: 3 },
  };
  if (action === "self-issue-update") return { ok: true, status: "draft_updated", draft: { ...baseDraft, ...payload, revision: 2 } };
  if (action === "self-issue-evaluation-start") return { ok: true, status: "assessment_requested", draft: { ...baseDraft, evidence_status: "collecting_live", evidence_collection_mode: "full", assessment_status: "waiting_for_evidence", enrichment: { evaluation_locked: true, evaluation_runs: [{ run_id: "evaluation-fixture", status: "running", draft_revision: 1 }] } } };
  if (action === "self-issue-evaluation-cancel") return { ok: true, status: "evidence_interrupted", draft: { ...baseDraft, revision: 2, evidence_status: "interrupted", assessment_status: "skipped", enrichment: { evaluation_locked: false, evaluation_runs: [{ run_id: "evaluation-fixture", status: "cancelled", draft_revision: 1 }] } } };
  if (action === "self-issue-suggestion-dismiss" || action === "self-issue-suggestion-resolve") {
    const current = response("self-issue-capture", { description: "Save hangs" });
    const draft = { ...current.draft as typeof baseDraft & { enrichment: Record<string, unknown> } };
    draft.enrichment = { ...draft.enrichment, suggestions: [] };
    return { ok: true, status: action.endsWith("dismiss") ? "suggestion_dismissed" : "suggestion_resolved", draft };
  }
  if (action === "self-issue-conflict-resolve") {
    const current = response("self-issue-capture", { description: "Save hangs" });
    const draft = { ...current.draft as typeof baseDraft & { enrichment: Record<string, unknown> } };
    draft.enrichment = { ...draft.enrichment, conflicts: [] };
    return { ok: true, status: "conflict_resolved", draft };
  }
  if (action === "self-issue-limited-continue") return { ok: true, status: "evidence_limited", draft: { ...baseDraft, evidence_status: "completed", evidence_collection_mode: "limited", assessment_status: "skipped", evidence_limit_reason: "user_selected_limited" } };
  if (action === "self-issue-preview") return { ok: true, status: "previewed", batch_id: "batch-fixture", payload_digest: "digest-fixture", publication_mode: "github", preview: { title: baseDraft.title, body: "## Describe the bug\n\nThe page freezes after Save." } };
  if (action === "self-issue-publish") {
    if (new URLSearchParams(window.location.search).get("github") === "partial") {
      return {
        ok: false,
        status: "partially_published",
        publication_state: "partially_published",
        intent_id: "intent-fixture",
        providers: { github: "partially_published" },
        intent_ids: { github: "intent-fixture" },
        issues: {
          github: {
            provider: "github",
            url: "https://github.com/owner/repo/issues/43",
            number: "43",
          },
        },
        reason: "github_labels_not_applied_http_503",
      };
    }
    if (new URLSearchParams(window.location.search).get("github") === "connected") {
      return {
        ok: true,
        status: "published",
        publication_state: "published",
        issues: {
          github: {
            provider: "github",
            url: "https://github.com/owner/repo/issues/42",
          },
        },
      };
    }
    return {
      ok: false,
      status: "authorization_required",
      provider: "github",
      draft_id: baseDraft.draft_id,
      batch_id: "batch-fixture",
    };
  }
  if (action === "self-issue-recover") return {
    ok: true,
    status: "published",
    publication_state: "published",
    issues: {
      github: {
        provider: "github",
        url: "https://github.com/owner/repo/issues/43",
        number: "43",
      },
    },
  };
  if (action === "self-issue-github-device-start") return {
    ok: true,
    status: "authorization_pending",
    provider: "github",
    transaction_id: "device-fixture",
    user_code: "ABCD-1234",
    verification_uri: "https://github.com/login/device",
    interval: 60,
  };
  if (action === "self-issue-get") return { ok: true, status: "draft_absent" };
  return { ok: true, status: "accepted", draft: baseDraft };
}

function Fixture() {
  const [actionResult, setActionResult] = useState<Record<string, unknown> | null>(null);
  const currentSnapshot = useMemo(snapshot, []);
  const onAction = async (action: string, payload: Record<string, unknown>) => {
    calls.push({ action, payload });
    const result = response(action, payload);
    setActionResult(result);
    return result;
  };
  return <>
    <OrchestratorPanel actionResult={actionResult as never} activeProjectId="fixture" context={{ taskId: "", traceId: "", pddId: "", fanoutId: "", stageId: "", targetRef: "", title: "" }} events={[]} focusSignal={0} panelMode="docked" visible onAction={onAction} onPanelModeChange={() => {}} onLockSession={() => {}} onSaveToken={() => {}} onUnlockSession={async () => ({ ok: true, status: "unlocked" })} snapshot={currentSnapshot} tokenPresent />
    <output id="fixture-calls">{JSON.stringify(calls)}</output>
  </>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
