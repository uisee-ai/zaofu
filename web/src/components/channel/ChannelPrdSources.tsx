import type { ChannelPrdDocument } from "../../api/channelDelivery";

/** The persisted PRD manifest describes inputs read, not a verified semantic classification. */
export function ChannelPrdSources({ document, onDiscuss }: { document: ChannelPrdDocument; onDiscuss: () => void }) {
  const sources = document.sources;
  const mappings = document.requirement_coverage ?? [];
  return <details className="channel-document-evidence channel-prd-inputs" data-testid="channel-prd-inputs">
    <summary>需求来源与映射{sources?.items.length ? ` · ${sources.items.length}` : ""}</summary>
    <p className="muted">以下是本版 PRD 绑定的讨论输入。需求映射由 Agent 提供，仍需对照原文核实。</p>
    {!sources || sources.status === "unavailable" ? <p>此版本没有可用的来源清单。</p> : null}
    {sources?.status === "incomplete" ? <p role="alert">部分来源无法验证，请查看具体条目。</p> : null}
    {sources?.status === "ready" && !sources.items.length ? <p>此版本没有可显示的 Owner 消息或文本附件。</p> : null}
    {sources?.items.map((item, index) => <details key={`${item.kind}:${item.message_id}:${item.digest}:${index}`}>
      <summary>{item.name || (item.kind === "requirement" ? "初始需求" : "讨论消息")} {item.message_id ? `· ${item.message_id}` : ""}</summary>
      <small>sha256:{item.digest}</small>
      {item.available ? <pre>{item.content}</pre> : <p>来源正文不可用或校验失败。</p>}
    </details>)}
    {mappings.length ? <details><summary>Agent 需求映射 · {mappings.length}</summary>
      {mappings.map((row, index) => <div key={String(row.id || index)}>
        <strong>{String(row.id || "需求")} · {String(row.status || "待核实")}</strong>
        {row.section ? <p>PRD 位置：{String(row.section)}</p> : null}
        {Array.isArray(row.source_refs) ? <p>来源：{row.source_refs.map(String).join("、")}</p> : null}
        {row.reason ? <p>{String(row.reason)}</p> : null}
      </div>)}
    </details> : null}
    <button type="button" className="icon-button" onClick={onDiscuss}>在讨论中补充或修正</button>
    <p className="muted">返回讨论发送更正；重新综合后生成的新 PRD 才会包含这些更改。</p>
  </details>;
}
