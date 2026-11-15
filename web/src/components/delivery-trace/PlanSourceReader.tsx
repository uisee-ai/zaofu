import type { PlanPreview } from "../../api/types";
import PlanSourceDocument from "./PlanSourceDocument";
import { planArtifactUrl } from "./planReviewModel";

export default function PlanSourceReader({ preview, projectId, name, anchor, onSelect }: {
  preview: PlanPreview; projectId: string; name: string; anchor: string; onSelect: (name: string, anchor: string) => void;
}) {
  const choices = (preview.artifacts || []).filter(p => /\.(md|markdown)$/i.test(p.ref));
  const selected = choices.find(p => p.name === name) || choices.find(p => p.name.startsWith("requirement_")) || choices[0];
  const binding = selected ? `${projectId}:${preview.plan_id}:${selected.name}:${selected.sha256}` : "";
  return <div className="plan-source-reader">
    <label className="plan-review-selector">同版来源文档<select value={selected?.name || ""} onChange={e => onSelect(e.target.value, "")}>
      {choices.map(p => <option key={p.name} value={p.name}>{p.ref}</option>)}
    </select></label>
    {!selected ? <p>本版未绑定 Markdown 来源文档；工程附件保留其他格式。</p> : <PlanSourceDocument key={binding}
      url={planArtifactUrl(projectId, preview.plan_id, selected.name)} sha256={selected.sha256}
      initialAnchor={anchor} onAnchorChange={next => onSelect(selected.name, next)} />}
  </div>;
}
