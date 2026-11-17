import { useEffect, useState } from "react";
import type { PlanPreview } from "../../api/types";
import { planArtifactUrl } from "./planReviewModel";

type Item = { name: string; ref: string; sha256: string; media_type: string; size_bytes: number | null;
  observed_state: string; version_binding: string; source_event_id: string; supersedes: string[] | null };
type Catalog = { items: Item[]; total: number; offset: number; next_offset: number | null };
const states: Record<string, string> = { available: "文件存在，正文未校验", empty: "当前文件为空，待核对版本",
  unavailable: "来源不可用", forbidden: "无权访问来源", unsupported: "不支持的来源类型", unknown: "来源状态未知" };

export default function PlanMaterialCatalog({ preview, projectId, onRead }: {
  preview: PlanPreview; projectId: string; onRead: (name: string, anchor: string) => void;
}) {
  const [offset, setOffset] = useState(0);
  const key = JSON.stringify([projectId, preview.plan_id, preview.requested_event_id, preview.resolved_event_id, preview.artifacts, offset]);
  const [result, setResult] = useState<{ key: string; catalog?: Catalog; error?: string }>({ key: "" });
  const current = result.key === key ? result : null;
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ offset: String(offset), limit: "50", request_event_id: preview.requested_event_id || "" });
    const url = `/api/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(preview.plan_id)}/preview/materials?${params}`;
    void fetch(url, { signal: controller.signal, cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error(`材料目录不可用（HTTP ${response.status}）`);
      const catalog = await response.json() as Catalog;
      if (!controller.signal.aborted) setResult({ key, catalog });
    }).catch(error => { if (!controller.signal.aborted) setResult({ key, error: error instanceof Error ? error.message : String(error) }); });
    return () => controller.abort();
  }, [key]);
  const catalog = current?.catalog;
  return <section aria-label="材料目录">
    <h2>材料目录</h2>
    <p>这里只展示本版绑定的材料与当前文件状态；正文尚未校验，目录完整不代表实现或验收完成。</p>
    {current?.error ? <p role="alert">{current.error}</p> : !catalog ? <p role="status">正在加载材料目录…</p> : <>
      <p>{catalog.total} 份材料 · 当前 {catalog.items.length ? catalog.offset + 1 : 0}–{catalog.offset + catalog.items.length}</p>
      {!catalog.total && <p>本版未提供材料绑定。</p>}
      {catalog.items.map(item => <article className="plan-review-card" style={{ overflowWrap: "anywhere" }} key={item.name}>
        <h3>{item.name}</h3><p>来源：{item.ref}</p>
        <p>类型：{item.media_type} · 大小：{item.size_bytes === null ? "未知" : `${item.size_bytes} bytes`}</p>
        <p>版本：{item.sha256 ? item.sha256 : "未固定版本"} · {states[item.observed_state] || states.unknown}</p>
        <p>来源记录：{item.source_event_id || "未知"} · 替代关系：{item.supersedes?.join("、") || "未声明"}</p>
        {(item.observed_state === "available" || item.observed_state === "empty") && ["text/markdown", "text/plain", "application/json"].includes(item.media_type) && <>
          {item.media_type === "text/markdown" && <button className="delivery-action-button" onClick={() => onRead(item.name, "")}>按章节阅读</button>}
          <a href={`${planArtifactUrl(projectId, preview.plan_id, item.name)}${item.sha256 ? `?sha256=${item.sha256}` : ""}`} target="_blank" rel="noreferrer">读取完整原文</a>
        </>}
        {!["text/markdown", "text/plain", "application/json"].includes(item.media_type) && <p>当前阅读器不支持此格式；材料引用仍保留。</p>}
      </article>)}
      <nav aria-label="材料目录分页">
        <button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>上一批材料</button>
        <button disabled={catalog.next_offset === null} onClick={() => setOffset(catalog.next_offset!)}>下一批材料</button>
      </nav>
    </>}
  </section>;
}
