import { Check, Copy } from "lucide-react";
import { lazy, Suspense, memo, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Streamdown, type StreamdownProps } from "streamdown";
import type { MarkdownTextProps } from "./MarkdownText";

import { ReferencePreviewContext } from "./referencePreview";
import { localDocumentLink } from "./previewRegistry";
import { prettyPrintIfJson } from "./jsonDisplay";

const MarkdownDiagram = lazy(() => import("./MarkdownDiagram"));

type MarkdownComponents = NonNullable<StreamdownProps["components"]>;

// How often the live (growing) bubble re-parses markdown while streaming.
// Without this, every token re-parses the whole accumulated text (jank on
// long/fast streams). ~11/s is smooth; trailing-edge so the final text still
// lands within this window of the last token.
const STREAM_MARKDOWN_THROTTLE_MS = 90;

/** Trailing-edge throttle: emits `value` at most once per `ms`. */
function useThrottledValue<T>(value: T, ms: number): T {
  const [throttled, setThrottled] = useState(value);
  const lastRef = useRef(0);
  const timerRef = useRef<number | undefined>(undefined);
  useEffect(() => {
    const now = typeof performance !== "undefined" ? performance.now() : Date.now();
    const wait = ms - (now - lastRef.current);
    if (wait <= 0) {
      lastRef.current = now;
      setThrottled(value);
      return undefined;
    }
    timerRef.current = window.setTimeout(() => {
      lastRef.current = typeof performance !== "undefined" ? performance.now() : Date.now();
      setThrottled(value);
    }, wait);
    return () => window.clearTimeout(timerRef.current);
  }, [value, ms]);
  return throttled;
}

const RichMarkdownText = memo(function RichMarkdownText({
  className = "",
  content,
  isStreaming = false,
}: MarkdownTextProps) {
  // Throttle only the streaming path; finalized text renders immediately so the
  // last token always appears (and a non-streaming bubble is never delayed).
  const openReference = useContext(ReferencePreviewContext);
  const throttledContent = useThrottledValue(content, STREAM_MARKDOWN_THROTTLE_MS);
  const text = isStreaming ? throttledContent : content;
  const normalized = useMemo(() => text.trim() || "-", [text]);
  const components = useMemo<MarkdownComponents>(() => ({
    h1: ({ children, ...props }) => <h1 {...props}>{children}</h1>,
    h2: ({ children, ...props }) => <h2 {...props}>{children}</h2>,
    h3: ({ children, ...props }) => <h3 {...props}>{children}</h3>,
    h4: ({ children, ...props }) => <h4 {...props}>{children}</h4>,
    h5: ({ children, ...props }) => <h5 {...props}>{children}</h5>,
    h6: ({ children, ...props }) => <h6 {...props}>{children}</h6>,
    a: ({ children, href, ...props }) => {
      const item = localDocumentLink(href || "");
      return openReference && item
        ? <button type="button" className="agent-inline-button" title="Preview file"
            onClick={() => openReference(item)}>{children}</button>
        : <a href={href} rel="noreferrer" target="_blank" {...props}>{children}</a>;
    },
    table: ({ children, node: _node, ...props }) => <MarkdownTable {...props}>{children}</MarkdownTable>,
    // A pre > code node is a block even without a language or trailing newline.
    // Inline code stays inline regardless of the length of its contents.
    pre: ({ node, children }) => {
      const code = node?.children.find(child => child.type === "element" && child.tagName === "code");
      if (!code || code.type !== "element") return <pre>{children}</pre>;
      const value = code.children.map(child => child.type === "text" ? child.value : "").join("");
      const language = languageFromClassName(String(code.properties.className ?? ""));
      return <CodeBlock code={trimTrailingNewline(language === "json" ? prettyPrintIfJson(value) : value)} language={language} />;
    },
    code: ({ children, node: _node, ...props }) => <code {...props}>{children}</code>,
  }), [openReference]);

  return (
    <div className={`agent-markdown ${isStreaming ? "streaming" : ""} ${className}`.trim()}>
      <Streamdown
        cdnUrl={null}
        components={components}
        controls={false}
        mode={isStreaming ? "streaming" : "static"}
        parseIncompleteMarkdown={isStreaming}
      >
        {normalized}
      </Streamdown>
    </div>
  );
});

export default RichMarkdownText;

function MarkdownTable({ children, ...props }: React.ComponentProps<"table">) {
  const wrapper = useRef<HTMLDivElement>(null);
  const table = useRef<HTMLTableElement>(null);
  const [overflow, setOverflow] = useState(false);
  useEffect(() => {
    const measure = () => setOverflow(Boolean(wrapper.current && table.current && table.current.scrollWidth > wrapper.current.clientWidth + 1));
    const observer = new ResizeObserver(measure);
    if (wrapper.current) observer.observe(wrapper.current);
    if (table.current) observer.observe(table.current);
    measure();
    return () => observer.disconnect();
  }, []);
  return <div className="agent-markdown-table">
    {overflow && <small className="agent-table-scroll-hint">表格较宽，可横向滚动 →</small>}
    <div ref={wrapper} className="agent-markdown-table-wrap" role="region" aria-label="表格，可横向滚动" tabIndex={0}>
      <table ref={table} {...props}>{children}</table>
    </div>
  </div>;
}

function CodeBlock({ code, language }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false);
  const [source, setSource] = useState(false);
  const fence = "`".repeat(Math.max(3, ...Array.from(code.matchAll(/`+/g), m => m[0].length + 1)));
  const handleCopy = async () => {
    if (!navigator.clipboard) return;
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <div className="agent-code-block agent-code-preview-card" data-preview-profile={language === "diff" ? "diff" : "code"}>
      <div className="agent-code-header">
        <span>{language || "text"}</span>
        <small>{code.split("\n").length.toLocaleString()} lines</small>
        {language === "mermaid" && <button type="button" onClick={() => setSource(!source)} aria-pressed={source}>{source ? "查看流程图" : "查看图源码"}</button>}
        <button type="button" onClick={handleCopy} title={copied ? "Copied" : "Copy code"}>
          {copied ? <Check size={13} /> : <Copy size={13} />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      {language === "mermaid" && !source
        ? <Suspense fallback={<p role="status">正在加载流程图…</p>}><MarkdownDiagram code={code} /></Suspense>
        : language && language !== "mermaid"
          ? <Streamdown mode="static" controls={false} cdnUrl={null}>{`${fence}${language}\n${code}\n${fence}`}</Streamdown>
          : <pre><code>{code as ReactNode}</code></pre>}
      {language === "mermaid" && !source && <details className="agent-diagram-source"><summary>流程图源码</summary><pre><code>{code}</code></pre></details>}
    </div>
  );
}

function languageFromClassName(className: string): string | undefined {
  const match = className.match(/language-([A-Za-z0-9_+-]+)/);
  return match?.[1];
}

function trimTrailingNewline(value: string): string {
  return value.replace(/\n$/, "");
}
