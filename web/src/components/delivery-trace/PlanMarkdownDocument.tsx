import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, Code, Download } from "lucide-react";
import { MarkdownText } from "../agent-session/MarkdownText";
import { planMarkdownPages } from "./planMarkdownPages";

export default function PlanMarkdownDocument({ content, reviewContent = "" }: { content: string; reviewContent?: string }) {
  const pages = useMemo(() => planMarkdownPages(content), [content]);
  const [selectedPage, setPage] = useState(0);
  const [showSource, setShowSource] = useState(false);
  const [continuous, setContinuous] = useState(true);
  const scrollPane = useRef<HTMLDivElement>(null);
  const index = Math.min(selectedPage, pages.length - 1);
  const current = pages[index];
  useEffect(() => {
    scrollPane.current?.scrollTo({ top: 0 });
  }, [index, content, continuous]);
  const download = () => {
    const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "plan.md";
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
          <button type="button" className="delivery-action-button" aria-label="下载 Markdown" title="下载 Markdown" onClick={download}>
            <Download size={14} aria-hidden="true" /><span>下载 Markdown</span>
          </button>
        </div>
      </nav>
      <div key={index} ref={scrollPane} className="plan-preview-markdown-pane" role="region" aria-label="计划正文" tabIndex={0}>
        {continuous
          ? showSource
            ? <pre className="plan-preview-markdown plan-document-source">{content}</pre>
            : <>
              {reviewContent ? <MarkdownText className="plan-preview-markdown plan-review-dossier" content={reviewContent} /> : null}
              {reviewContent ? <hr className="plan-review-divider" /> : null}
              <MarkdownText className="plan-preview-markdown" content={content} />
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
