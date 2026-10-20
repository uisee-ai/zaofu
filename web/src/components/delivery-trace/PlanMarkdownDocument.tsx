import { useEffect, useMemo, useRef, useState } from "react";
import { MarkdownText } from "../agent-session/MarkdownText";
import { planMarkdownPages } from "./planMarkdownPages";

export default function PlanMarkdownDocument({ content }: { content: string }) {
  const pages = useMemo(() => planMarkdownPages(content), [content]);
  const [selectedPage, setPage] = useState(0);
  const [showSource, setShowSource] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const index = Math.min(selectedPage, pages.length - 1);
  const current = pages[index];
  useEffect(() => {
    root.current?.closest(".plan-preview-markdown-pane")?.scrollTo({ top: 0 });
  }, [index, content]);
  const download = () => {
    const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "plan.md";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1600);
  };
  return (
    <div ref={root} className="plan-document">
      <nav className="plan-document-toolbar" aria-label="计划阅读导航">
        {pages.length > 1 && <>
          <button type="button" className="delivery-action-button" disabled={index === 0} onClick={() => setPage(index - 1)}>上一页</button>
          <label>章节
            <select aria-label="计划章节" value={index} onChange={(event) => setPage(Number(event.target.value))}>
              {pages.map((page, i) => <option key={i} value={i}>{i + 1}. {page.title}</option>)}
            </select>
          </label>
          <span>第 {index + 1} / {pages.length} 页</span>
          <button type="button" className="delivery-action-button" disabled={index === pages.length - 1} onClick={() => setPage(index + 1)}>下一页</button>
        </>}
        <button type="button" className="delivery-action-button" aria-pressed={showSource} onClick={() => setShowSource(!showSource)}>
          {showSource ? "格式化阅读" : "查看源码"}
        </button>
        <button type="button" className="delivery-action-button" onClick={download}>下载 Markdown</button>
      </nav>
      {current.plain && !showSource && <p className="muted" role="status">当前段以原文显示；全文可下载。</p>}
      {showSource || current.plain
        ? <pre className="plan-preview-markdown plan-document-source">{current.source}</pre>
        : <MarkdownText className="plan-preview-markdown" content={current.markdown} />}
    </div>
  );
}
