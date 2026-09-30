import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { SelfIssueSessionWorkspace } from "../../../src/components/orchestrator/SelfIssueSessionWorkspace";
import "../../../src/styles.css";

type Data = Record<string, any>;
const initial: Data = {
  session_id: "issue-session-fixture", session_status: "ready_for_confirmation",
  report: { title: "Original", actual_behavior: "Request pending", classification: "bug" },
  working_set: { evidences: [{ ref: "e1", summary: "Browser observed pending" }] },
  workflow: { label: "报告已完成", activity_log: [{ id: "activity-1", label: "Read file", tool: "exec_command", command: "rg pending", result_summary: "first 1000", result_ref: { ref: "result" } }] },
};
const calls: Data[] = [];
function Fixture() {
  const [card, setCard] = useState<Data>(() => JSON.parse(localStorage.getItem("portable-fixture") || "null") || initial);
  const [closed, setClosed] = useState(false);
  const [minimized, setMinimized] = useState(false);
  // Frequent projection polling must neither reset autosave nor erase edits.
  useEffect(() => { const id = setInterval(() => setCard((current) => ({ ...current })), 1000); return () => clearInterval(id); }, []);
  const update = (value: Data) => setCard((current) => {
    const next = { ...current, ...value }; localStorage.setItem("portable-fixture", JSON.stringify(next)); return next;
  });
  const action = async (name: string, payload: Data) => {
    calls.push({ action: name, payload });
    if (name === "self-issue-session-get") return { ok: true, ...card };
    if (name === "self-issue-session-save-draft" && localStorage.getItem("portable-fail-save")) return { ok: false, reason: "模拟网络不可用" };
    if (name === "self-issue-session-materialize") {
      const version = Number(card.editor?.revision || 0) + 1;
      return { ok: true, editor: { draft_id: "draft-fixture", revision: version, title: card.report.title, body: "## Description\nRequest pending\n", versions: [...(card.editor?.versions || []), version], publication_state: "draft" } };
    }
    if (name === "self-issue-session-save-draft") return { ok: true, editor: { ...card.editor, title: payload.title, body: payload.body, revision: payload.revision + 1, versions: [...card.editor.versions, payload.revision + 1] } };
    if (name === "self-issue-session-activity-result") return { ok: true, body: "a".repeat(20000) + "END OF FULL RESULT" };
    if (name === "self-issue-session-reanalyze") return { ok: true, session_status: "investigating", workflow: { ...card.workflow, reanalysis_history: [...(card.workflow.reanalysis_history || []), { report: card.report, evidences: card.working_set.evidences, activity_log: card.workflow.activity_log }], activity_log: [], label: "再次分析" } };
    if (name === "self-issue-session-pause") return { ok: true, session_status: "paused", workflow: { ...card.workflow, interruption_source: "user", reason: "用户中断分析" } };
    if (name === "self-issue-session-retry") return { ok: true, session_status: "ready_for_confirmation", report: { ...card.report, actual_behavior: "Verified pending" }, workflow: { ...card.workflow, label: "再次分析已完成" } };
    if (name === "self-issue-preview") return { ok: true, batch_id: "batch-1" };
    if (name === "self-issue-publish") return { ok: true, status: "published", editor: { ...card.editor, publication_state: "published" } };
    return { ok: true };
  };
  return <>{minimized && !closed ? <button onClick={() => setMinimized(false)}>恢复 Issue 会话</button> : null}
    {closed ? <p>closed</p> : <div hidden={minimized}><SelfIssueSessionWorkspace card={card} onAction={action} onUpdate={update} onClose={() => { localStorage.removeItem("portable-fixture"); setClosed(true); }} onMinimize={() => setMinimized(true)} publicationMode="github" /></div>}
    <output id="calls">{JSON.stringify(calls)}</output></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
