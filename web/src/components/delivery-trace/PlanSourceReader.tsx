import { useEffect, useState } from "react";
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
    {!selected ? <p>本版未绑定 Markdown 来源文档；工程附件保留其他格式。</p> : <VersionedSource key={binding}
      url={planArtifactUrl(projectId, preview.plan_id, selected.name)} sha256={selected.sha256}
      initialAnchor={anchor} onAnchorChange={next => onSelect(selected.name, next)} />}
  </div>;
}


function VersionedSource({ url, sha256, initialAnchor, onAnchorChange }: {
  url: string; sha256: string; initialAnchor: string; onAnchorChange: (anchor: string) => void;
}) {
  const [observed, setObserved] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (sha256) return;
    const controller = new AbortController();
    void fetch(`${url}/version`, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(`来源版本不可用（HTTP ${response.status}）`);
      const body = await response.json() as { sha256: string };
      if (!/^[a-f0-9]{64}$/.test(body.sha256)) throw new Error("来源缺少有效版本");
      if (!controller.signal.aborted) setObserved(body.sha256);
    }).catch(error => { if (!controller.signal.aborted) setError(String(error instanceof Error ? error.message : error)); });
    return () => controller.abort();
  }, [url, sha256]);
  if (error) return <p role="alert">{error}</p>;
  if (!sha256 && !observed) return <p role="status">正在核对选定来源的当前版本…</p>;
  return <>
    {!sha256 && <p role="status">旧版引用未固定审批时摘要；以下为本次读取快照。</p>}
    <PlanSourceDocument url={url} sha256={sha256 || observed} initialAnchor={initialAnchor} onAnchorChange={onAnchorChange} />
  </>;
}
