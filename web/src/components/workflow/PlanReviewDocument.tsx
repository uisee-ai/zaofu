import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { MarkdownText } from "../agent-session/MarkdownText";
import { previewQuote } from "./workflowReviewModel";
import { planReviewBlocks, selectedPlanBlocks, type PlanQuote } from "./planReviewDocument";
import { planMarkdownPages } from "../delivery-trace/planMarkdownPages";

export default function PlanReviewDocument({ body, disabled, onSelect, agentControls }: {
  body: string; disabled: boolean;
  onSelect: (quote: PlanQuote | null, rect?: DOMRect) => void;
  agentControls: ReactNode;
}) {
  const [raw, setRaw] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const select = useRef(onSelect); select.current = onSelect;
  const blocks = useMemo(() => {
    const { blocks, definitions } = planReviewBlocks(body);
    return blocks.map(block => ({ ...block, pages: planMarkdownPages(block.source + definitions) }));
  }, [body]);
  useEffect(() => {
    let frame = 0;
    const capture = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (disabled) { select.current(null); return; }
        const selection = window.getSelection();
        const quote = raw ? previewQuote(selection, root.current) : selectedPlanBlocks(selection, root.current, body);
        select.current(quote, quote ? selection!.getRangeAt(0).getBoundingClientRect() : undefined);
      });
    };
    const dismiss = () => { cancelAnimationFrame(frame); select.current(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") dismiss(); };
    document.addEventListener("selectionchange", capture);
    document.addEventListener("scroll", dismiss, true);
    document.addEventListener("keydown", escape);
    window.addEventListener("resize", dismiss);
    dismiss();
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("selectionchange", capture);
      document.removeEventListener("scroll", dismiss, true);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("resize", dismiss);
    };
  }, [body, raw, disabled]);
  return <>
    <nav className="plan-source-toolbar" aria-label="Plan document view">
      <button type="button" title="格式化阅读" aria-pressed={!raw} onClick={() => { setRaw(false); onSelect(null); }}>Preview</button>
      <button type="button" title="Markdown 源码" aria-pressed={raw} onClick={() => { setRaw(true); onSelect(null); }}>Raw</button>
      {agentControls}
      <small>选中文字，点击浮出的「引用到 Agent」提出修改</small>
    </nav>
    <div ref={root} className="plan-bound-document" data-testid="plan-review-document">
      {raw ? <pre className="plan-artifact-source" data-review-field="plan-artifact" data-testid="plan-review-source" tabIndex={0}>{body}</pre>
        : blocks.map(block => <section className="plan-review-block" key={block.start}>
          <div data-plan-block data-source-start={block.start} data-source-end={block.end}>
            {block.pages.map((page, index) => page.plain
              ? <pre key={index} className="plan-document-source">{page.source}</pre>
              : <MarkdownText key={index} content={page.markdown} />)}
          </div>
        </section>)}
    </div>
  </>;
}
