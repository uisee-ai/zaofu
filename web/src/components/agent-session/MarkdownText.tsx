import { lazy, memo, Suspense, useState } from "react";
import { standaloneJsonMarkdown } from "./jsonDisplay.js";
import { MAX_PLAINTEXT_DISPLAY_LENGTH, isPathologicalText } from "./markdownGuard";

export interface MarkdownTextProps {
  className?: string;
  content: string;
  formatStandaloneJson?: boolean;
  isStreaming?: boolean;
}

const RichMarkdownText = lazy(() => import("./RichMarkdownText"));

export const MarkdownText = memo(function MarkdownText({
  className = "",
  content,
  formatStandaloneJson = false,
  isStreaming = false,
}: MarkdownTextProps) {
  // Defense-in-depth: a huge or unbroken-token payload (e.g. a base64 data
  // URL serialized into the stream) would lock the tab in the markdown
  // pipeline + layout. Render it as plain, break-anywhere text instead.
  if (isPathologicalText(content)) {
    return <PlainTextGuard className={className} content={content} />;
  }
  const renderedContent = formatStandaloneJson
    ? standaloneJsonMarkdown(content)
    : content;
  return (
    <Suspense fallback={<MarkdownFallback className={className} content={renderedContent} />}>
      <RichMarkdownText className={className} content={renderedContent} isStreaming={isStreaming} />
    </Suspense>
  );
});

function PlainTextGuard({ className = "", content }: Pick<MarkdownTextProps, "className" | "content">) {
  const [selectedPage, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(content.length / MAX_PLAINTEXT_DISPLAY_LENGTH));
  const page = Math.min(selectedPage, pages - 1);
  const boundary = (index: number) => {
    const offset = index * MAX_PLAINTEXT_DISPLAY_LENGTH;
    const code = content.charCodeAt(offset);
    return code >= 0xdc00 && code <= 0xdfff ? offset - 1 : offset;
  };
  const shown = content.slice(boundary(page), boundary(page + 1));
  const download = () => {
    const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "channel-message.md";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return (
    <div className={`agent-markdown ${className}`.trim()}>
      {pages > 1 && (
        <nav aria-label="长消息分页">
          <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button>
          <span>完整正文，第 {page + 1} / {pages} 页</span>
          <button type="button" disabled={page + 1 === pages} onClick={() => setPage(page + 1)}>下一页</button>
          <button type="button" onClick={download}>下载完整正文</button>
        </nav>
      )}
      <pre className="agent-markdown-plain-guard">{shown}</pre>
    </div>
  );
}

// Shown only for the brief window while the RichMarkdownText chunk is loading
// (or if it fails to load). Strip the loudest markdown markers so it reads as
// plain prose instead of flashing raw `**bold**` / backticks / `- ` lists.
function stripMarkdownMarkers(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, (m) => m.replace(/```[^\n]*\n?/g, "").replace(/```/g, ""))
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1$2")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "• ");
}

function MarkdownFallback({ className = "", content }: Pick<MarkdownTextProps, "className" | "content">) {
  return (
    <div className={`agent-markdown ${className}`.trim()}>
      <p style={{ whiteSpace: "pre-wrap" }}>{stripMarkdownMarkers(content).trim() || "-"}</p>
    </div>
  );
}
