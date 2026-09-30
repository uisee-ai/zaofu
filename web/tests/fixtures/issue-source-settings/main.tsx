import React from "react";
import { createRoot } from "react-dom/client";
import { IssueSourceSettings } from "../../../src/components/projection/IssueSourceSettings";
import "../../../src/styles.css";
import "../../../src/settings/settings.css";

type Data = Record<string, any>;
const current = (): Data => JSON.parse(localStorage.getItem("issue-source") || 'null') || {
  source_mode: "current_project", configured_root: "", config_digest: "v1",
};
const inspect = (setting: Data) => ({
  source_root: setting.source_mode === "explicit_target" ? setting.source_root || setting.configured_root : "/workspace/fixture",
  git_root: "/workspace/product", remote: "https://github.com/example/product.git",
  branch: "dev", revision: "abc123", dirty: true, is_git: true,
});
async function action(name: string, payload: Data) {
  const publication_targets = localStorage.getItem("no-publication-target") ? [] : [{ provider: "github", project: "example/issue-reports" }];
  if (name === "self-issue-source-get") return { ok: true, ...current(), publication_targets, valid: true, inspection: inspect(current()) };
  if (name === "self-issue-source-inspect") return { ok: true, ...payload, publication_targets, config_digest: current().config_digest,
    valid: payload.source_root !== "/missing", reason: "源码目录不存在", inspection: inspect(payload) };
  if (localStorage.getItem("simulate-config-conflict")) return { ok: false, status: "config_conflict", reason: "zf.yaml 已变化，请重新读取并检查后保存" };
  const saved = { source_mode: payload.source_mode, configured_root: payload.source_root, config_digest: "v2" };
  localStorage.setItem("issue-source", JSON.stringify(saved));
  return { ok: true, ...saved, publication_targets, valid: true, inspection: inspect(saved) };
}
createRoot(document.getElementById("root")!).render(<main className="workspace"><div className="settings-root">
  <IssueSourceSettings projectId="fixture" actionReady onAction={action} />
</div></main>);
