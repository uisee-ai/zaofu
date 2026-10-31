import { useEffect, useState } from "react";
import type { PlanPreview } from "../../api/types";
import PlanMarkdownDocument from "./PlanMarkdownDocument";
import { planArtifactUrl } from "./planReviewModel";

export default function PlanSourceReader({ preview, projectId, name, anchor, onSelect }: {
  preview: PlanPreview; projectId: string; name: string; anchor: string; onSelect: (name: string, anchor: string) => void;
}) {
  const choices = (preview.artifacts || []).filter(p => /\.md$/.test(p.ref));
  const selected = choices.find(p => p.name === name) || choices.find(p => p.name.startsWith("requirement_")) || choices[0];
  const binding = selected ? `${selected.name}:${selected.sha256}` : "";
  const [document, setDocument] = useState<{ binding: string; body: string; error: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setDocument(null);
    if (selected) void fetch(planArtifactUrl(projectId, preview.plan_id, selected.name), { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.text();
      if (!controller.signal.aborted) setDocument({ binding, body, error: "" });
    }).catch(e => { if (!controller.signal.aborted) setDocument({ binding, body: "", error: String(e) }); });
    return () => controller.abort();
  }, [binding, projectId, preview.plan_id]);
  return <div className="plan-source-reader">
    <label className="plan-review-selector">同版来源文档<select value={selected?.name || ""} onChange={e => onSelect(e.target.value, "")}>
      {choices.map(p => <option key={p.name} value={p.name}>{p.ref}</option>)}
    </select></label>
    {!selected ? <p>本版未绑定 Markdown 来源文档；工程附件保留其他格式。</p> : document?.binding !== binding ? <p role="status">正在读取同版原文…</p> : document.error ? <p role="alert">来源读取失败：{document.error}</p> : <PlanMarkdownDocument key={binding} content={document.body} initialAnchor={anchor} onAnchorChange={next => onSelect(selected.name, next)} />}
  </div>;
}
