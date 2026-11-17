import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, Code, Download } from "lucide-react";
import { MarkdownText } from "../agent-session/MarkdownText";
import { parsePlanMarkdown, planMarkdownPages } from "./planMarkdownPages";
import { planDocumentSections } from "./planDocumentSections";

export default function PlanMarkdownDocument({ content, reviewContent = "", initialAnchor = "", onAnchorChange }: { content: string; reviewContent?: string; initialAnchor?: string; onAnchorChange?: (anchor: string) => void }) {
  const tree = useMemo(() => parsePlanMarkdown(content), [content]);
  const pages = useMemo(() => planMarkdownPages(content, tree), [content, tree]);
  const sections = useMemo(() => planDocumentSections(content, tree), [content, tree]);
  const [search, setSearch] = useState("");
  const [anchor, setAnchor] = useState(initialAnchor);
  const [selectedPage, setPage] = useState(0);
  const [showSource, setShowSource] = useState(false);
  const [continuous, setContinuous] = useState(true);
  const scrollPane = useRef<HTMLDivElement>(null);
  const index = Math.min(selectedPage, pages.length - 1);
  const current = pages[index];
  useEffect(() => {
    scrollPane.current?.scrollTo({ top: 0 });
  }, [index, content, continuous]);
  useEffect(() => { setAnchor(initialAnchor); }, [initialAnchor]);
  useEffect(() => {
    if (anchor && continuous) scrollPane.current?.querySelector(`[data-plan-anchor="${CSS.escape(anchor)}"]`)?.scrollIntoView({ block: "start" });
  }, [anchor, continuous, content]);
  const jump = (value: string) => {
    setContinuous(true); setShowSource(false); setAnchor(value);
    onAnchorChange?.(value);
    requestAnimationFrame(() => scrollPane.current?.querySelector(`[data-plan-anchor="${CSS.escape(value)}"]`)?.scrollIntoView({ block: "start" }));
  };
  const download = (includeReview = false) => {
    const downloadContent = includeReview && reviewContent
      ? `${reviewContent.trimEnd()}\n\n---\n\n${content}`
      : content;
    const url = URL.createObjectURL(new Blob([downloadContent], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = includeReview ? "plan-review.md" : "plan.md";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1600);
  };
  return (
    <div className="plan-document">
      <nav className="plan-document-toolbar" aria-label="计划阅读导航">
        <button
          type="button"
          className="delivery-action-button"
          aria-pressed={continuous}
          onClick={() => setContinuous(!continuous)}
          title={continuous ? "切换到章节分页" : "切换到全文连续阅读"}
        >
          <BookOpen size={14} aria-hidden="true" />
          <span>{continuous ? "全文连续阅读" : "章节分页"}</span>
        </button>
        {!continuous && pages.length > 1 && <>
          <label>章节
            <select aria-label="计划章节" value={index} onChange={(event) => setPage(Number(event.target.value))}>
              {pages.map((page, i) => <option key={i} value={i}>{i + 1}. {page.title}</option>)}
            </select>
          </label>
          <div className="plan-document-pagination">
            <button type="button" className="delivery-action-button" disabled={index === 0} onClick={() => setPage(index - 1)}>上一页</button>
            <span aria-live="polite">第 {index + 1} / {pages.length} 页</span>
            <button type="button" className="delivery-action-button" disabled={index === pages.length - 1} onClick={() => setPage(index + 1)}>下一页</button>
          </div>
        </>}
        <div className="plan-document-actions">
          <button type="button" className="delivery-action-button" aria-label={showSource ? "格式化阅读" : "查看源码"} title={showSource ? "格式化阅读" : "查看源码"} aria-pressed={showSource} onClick={() => setShowSource(!showSource)}>
            {showSource ? <BookOpen size={14} aria-hidden="true" /> : <Code size={14} aria-hidden="true" />}
            <span>{showSource ? "格式化阅读" : "查看源码"}</span>
          </button>
          <button type="button" className="delivery-action-button" aria-label="下载 Markdown" title="下载原始 Plan Markdown" onClick={() => download()}>
            <Download size={14} aria-hidden="true" /><span>下载 Markdown</span>
          </button>
          {reviewContent && <button type="button" className="delivery-action-button" aria-label="下载审核包" title="下载审核包" onClick={() => download(true)}>
            <Download size={14} aria-hidden="true" /><span>下载审核包</span>
          </button>}
        </div>
      </nav>
      <details className="plan-document-outline">
        <summary>章节索引</summary>
        <label>搜索正文<input type="search" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <p>{sections.filter(section => section.content.toLowerCase().includes(search.toLowerCase())).length} 个匹配章节</p>
        <ol>
          {reviewContent ? <li><button type="button" onClick={() => { setContinuous(true); scrollPane.current?.scrollTo({ top: 0 }); }}>人工审批摘要</button></li> : null}
          {sections.filter(section => section.content.toLowerCase().includes(search.toLowerCase())).map(section => <li key={section.key}><button type="button" onClick={() => jump(section.anchor)}>{section.title}</button></li>)}
        </ol>
      </details>
      <div key={index} ref={scrollPane} className="plan-preview-markdown-pane" role="region" aria-label="计划正文" tabIndex={0}>
        {continuous
          ? showSource
            ? <pre className="plan-preview-markdown plan-document-source">{content}</pre>
            : <>
              {reviewContent ? <details className="plan-review-card"><summary>人工审批摘要与工程审计</summary><MarkdownText className="plan-preview-markdown plan-review-dossier" content={reviewContent} /></details> : null}
              <ContinuousPlanBody sections={sections} documentPages={pages} anchor={anchor} scrollPane={scrollPane} />
            </>
          : <>
            {current.plain && !showSource && <p className="muted" role="status">当前段以原文显示；全文可下载。</p>}
            {showSource || current.plain
              ? <pre className="plan-preview-markdown plan-document-source">{current.source}</pre>
              : <MarkdownText className="plan-preview-markdown" content={current.markdown} />}
          </>}
      </div>
    </div>
  );
}

// Keep the toolbar and first readable pages available while later Markdown is
// rendered on demand. The full source and download remain independently usable.
function ContinuousPlanBody({ sections, documentPages, anchor, scrollPane }: {
  sections: ReturnType<typeof planDocumentSections>; anchor: string;
  documentPages: ReturnType<typeof planMarkdownPages>;
  scrollPane: { current: HTMLDivElement | null };
}) {
  const pages = useMemo(() => sections.flatMap(section =>
    (sections.length === 1 ? documentPages : planMarkdownPages(section.content)).map((page, index) => ({
      ...page, anchor: index === 0 ? section.anchor : "", key: `${section.key}:${index}`,
    }))), [sections, documentPages]);
  const [visible, setVisible] = useState(4);
  const sentinel = useRef<HTMLButtonElement>(null);
  useEffect(() => setVisible(4), [pages]);
  const target = anchor ? pages.findIndex(page => page.anchor === anchor) : -1;
  const count = Math.max(visible, target + 1);
  useEffect(() => {
    if (target >= 0) {
      setVisible(value => Math.max(value, target + 1));
      requestAnimationFrame(() => scrollPane.current?.querySelector(`[data-plan-anchor="${CSS.escape(anchor)}"]`)?.scrollIntoView({ block: "start" }));
    }
  }, [target, anchor, scrollPane]);
  useEffect(() => {
    const button = sentinel.current;
    if (!button || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) setVisible(value => Math.min(value + 2, pages.length));
    }, { root: button.closest(".plan-preview-markdown-pane"), rootMargin: "200px" });
    observer.observe(button);
    return () => observer.disconnect();
  }, [count, pages.length]);
  return <>
    {pages.slice(0, count).map(page => <section key={page.key} data-plan-anchor={page.anchor || undefined}>
      {page.plain
        ? <pre className="plan-preview-markdown plan-document-source">{page.source}</pre>
        : <MarkdownText className="plan-preview-markdown" content={page.markdown} />}
    </section>)}
    {count < pages.length && <button ref={sentinel} type="button" className="delivery-action-button"
      onClick={() => setVisible(value => Math.min(value + 2, pages.length))}>
      继续加载正文（还有 {pages.length - count} 页）；完整原文可下载
    </button>}
  </>;
}
