import { Check, Copy } from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Streamdown, type StreamdownProps } from "streamdown";
import type { MarkdownTextProps } from "./MarkdownText";

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
  const throttledContent = useThrottledValue(content, STREAM_MARKDOWN_THROTTLE_MS);
  const text = isStreaming ? throttledContent : content;
  const normalized = useMemo(() => text.trim() || "-", [text]);
  const components = useMemo<MarkdownComponents>(() => ({
    h1: ({ children, ...props }) => <h2 {...props}>{children}</h2>,
    h2: ({ children, ...props }) => <h2 {...props}>{children}</h2>,
    h3: ({ children, ...props }) => <h3 {...props}>{children}</h3>,
    h4: ({ children, ...props }) => <h4 {...props}>{children}</h4>,
    h5: ({ children, ...props }) => <h5 {...props}>{children}</h5>,
    h6: ({ children, ...props }) => <h5 {...props}>{children}</h5>,
    a: ({ children, href, ...props }) => (
      <a href={href} rel="noreferrer" target="_blank" {...props}>
        {children}
      </a>
    ),
    table: ({ children, ...props }) => (
      <div className="agent-markdown-table-wrap">
        <table {...props}>{children}</table>
      </div>
    ),
    pre: ({ children }) => <>{children}</>,
    code: ({ children, className: codeClassName, ...props }) => {
      const value = String(children ?? "");
      const language = languageFromClassName(String(codeClassName ?? ""));
      const isBlock = Boolean(language) || value.includes("\n") || value.length > 96;
      if (!isBlock) {
        return <code className={codeClassName} {...props}>{children}</code>;
      }
      return <CodeBlock code={trimTrailingNewline(value)} language={language} />;
    },
  }), []);

  return (
    <div className={`agent-markdown ${isStreaming ? "streaming" : ""} ${className}`.trim()}>
      <Streamdown
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

function CodeBlock({ code, language }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false);
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
        <button type="button" onClick={handleCopy} title={copied ? "Copied" : "Copy code"}>
          {copied ? <Check size={13} /> : <Copy size={13} />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre>
        <code>{code as ReactNode}</code>
      </pre>
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
