import { useCallback, useEffect, useRef, useState } from "react";
import { MarkdownText } from "../agent-session/MarkdownText";

type Chapter = { id: number; title: string; anchor: string; page_count: number };
type Index = { sections: Chapter[]; section_count: number; selected_section: number | null; anchor_found: boolean | null; offset: number; next_offset: number | null; query: string };
type Part = { content: string; title: string; anchor: string; section: number; page: number; page_count: number; section_complete: boolean };

async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(`HTTP ${response.status}: ${detail.detail || "读取失败"}`);
  }
  return response.json();
}

export default function PlanSourceDocument({ url, sha256, initialAnchor, onAnchorChange }: {
  url: string; sha256: string; initialAnchor: string; onAnchorChange: (anchor: string) => void;
}) {
  const [index, setIndex] = useState<Index | null>(null);
  const [indexError, setIndexError] = useState("");
  const [indexLoading, setIndexLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [position, setPosition] = useState<{ section: number; page: number } | null>(null);
  const [result, setResult] = useState<{ key: string; part?: Part; error?: string } | null>(null);
  const selectedAnchor = useRef<string | null>(null);
  const indexRequest = useRef<AbortController | null>(null);
  const scrollPane = useRef<HTMLDivElement>(null);
  const anchorCallback = useRef(onAnchorChange);
  anchorCallback.current = onAnchorChange;
  const binding = `sha256=${encodeURIComponent(sha256)}`;
  const section = position?.section;
  const page = position?.page;
  const key = `${section}:${page}`;
  const part = result?.key === key ? result.part : undefined;
  const error = result?.key === key ? result.error : undefined;
  const loadIndex = useCallback((query: string, select = false) => {
    indexRequest.current?.abort();
    const controller = new AbortController();
    indexRequest.current = controller;
    setIndexLoading(true); setIndexError("");
    if (select) { setPosition(null); setIndex(null); }
    void read<Index>(`${url}/document?${binding}&${query}`, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setIndex(value);
      if (select) {
        if (value.anchor_found === false) setIndexError("本版没有该章节；请从目录重新选择。");
        else setPosition({ section: value.selected_section ?? 0, page: 0 });
      }
    }).catch(error => { if (!controller.signal.aborted) setIndexError(String(error)); })
      .finally(() => { if (!controller.signal.aborted) setIndexLoading(false); });
  }, [url, binding]);
  useEffect(() => {
    if (selectedAnchor.current === initialAnchor) return;
    selectedAnchor.current = initialAnchor;
    loadIndex(`anchor=${encodeURIComponent(initialAnchor)}`, true);
  }, [initialAnchor, loadIndex]);
  useEffect(() => () => {
    indexRequest.current?.abort();
    selectedAnchor.current = null; // StrictMode effect replay must restart an aborted index read.
  }, []);
  useEffect(() => {
    if (section === undefined || page === undefined) return;
    const controller = new AbortController();
    setResult(null);
    void read<Part>(`${url}/document/sections/${section}?${binding}&page=${page}`, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setResult({ key, part: value });
      selectedAnchor.current = value.anchor;
      anchorCallback.current(value.anchor);
      scrollPane.current?.scrollTo({ top: 0 });
    }).catch(error => { if (!controller.signal.aborted) setResult({ key, error: String(error) }); });
    return () => controller.abort();
  }, [url, binding, section, page, key]);
  const choose = (section: number) => { setIndexError(""); setPosition({ section, page: 0 }); };
  return <div className="plan-source-reader">
    <nav className="plan-document-toolbar" aria-label="来源阅读导航">
      <label>章节<select aria-label="来源章节" value={section ?? ""} onChange={event => choose(Number(event.target.value))}>
        {section === undefined && <option value="">选择章节</option>}
        {section !== undefined && !index?.sections.some(row => row.id === section) && <option value={section}>{part?.title || `章节 ${section + 1}`}</option>}
        {index?.sections.map(row => <option key={row.id} value={row.id}>{row.id + 1}. {row.title}</option>)}
      </select></label>
      <button className="delivery-action-button" type="button" disabled={section === undefined || section === 0} onClick={() => choose(section! - 1)}>上一章</button>
      <button className="delivery-action-button" type="button" disabled={section === undefined || !index || section + 1 >= index.section_count} onClick={() => choose(section! + 1)}>下一章</button>
      <a href={`${url}?${binding}`} target="_blank" rel="noreferrer">完整原文 / 下载</a>
    </nav>
    <details className="plan-document-outline"><summary>章节索引</summary>
      <form onSubmit={event => { event.preventDefault(); loadIndex(`q=${encodeURIComponent(search)}`); }}>
        <label>搜索正文<input type="search" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <button className="delivery-action-button" type="submit">搜索章节</button>
      </form>
      {index && <>
        <p>{index.query ? `${index.sections.length} 个匹配章节${index.next_offset !== null ? "（还有更多）" : ""}` : `共 ${index.section_count} 章`} · 当前仅加载所选章节</p>
        <ol>{index.sections.map(row => <li key={row.id}><button className="delivery-action-button" type="button" onClick={() => choose(row.id)}>{row.title}</button></li>)}</ol>
        <button className="delivery-action-button" type="button" disabled={index.offset === 0 || indexLoading} onClick={() => loadIndex(`offset=${index.query ? 0 : Math.max(0, index.offset - 100)}&q=${encodeURIComponent(index.query)}`)}>前一组目录</button>
        <button className="delivery-action-button" type="button" disabled={index.next_offset === null || indexLoading} onClick={() => loadIndex(`offset=${index.next_offset}&q=${encodeURIComponent(index.query)}`)}>后一组目录</button>
      </>}
    </details>
    {indexLoading && <p role="status">正在读取章节索引…</p>}
    {indexError && <p role="alert">来源索引不可用：{indexError}</p>}
    {part && part.page_count > 1 && <nav className="plan-document-toolbar" aria-label="章节片段导航">
      <span>长章节原文片段 {part.page + 1} / {part.page_count}；当前未显示整章</span>
      <button className="delivery-action-button" type="button" disabled={part.page === 0} onClick={() => setPosition({ section: part.section, page: part.page - 1 })}>上一片段</button>
      <button className="delivery-action-button" type="button" disabled={part.page + 1 === part.page_count} onClick={() => setPosition({ section: part.section, page: part.page + 1 })}>下一片段</button>
    </nav>}
    <div ref={scrollPane} className="plan-preview-markdown-pane" role="region" aria-label="来源正文" tabIndex={0}>
      {error ? <p role="alert">来源读取失败：{error}</p> : part ? part.section_complete
        ? <MarkdownText className="plan-preview-markdown" content={part.content} />
        : <pre className="plan-preview-markdown plan-document-source">{part.content}</pre>
        : position && <p role="status">正在读取同版章节…</p>}
    </div>
  </div>;
}
