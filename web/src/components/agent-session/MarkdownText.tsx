import { lazy, memo, Suspense } from "react";
import { clampPlainText, isPathologicalText } from "./markdownGuard";

export interface MarkdownTextProps {
  className?: string;
  content: string;
  isStreaming?: boolean;
}

const RichMarkdownText = lazy(() => import("./RichMarkdownText"));

export const MarkdownText = memo(function MarkdownText({
  className = "",
  content,
  isStreaming = false,
}: MarkdownTextProps) {
  // Defense-in-depth: a huge or unbroken-token payload (e.g. a base64 data
  // URL serialized into the stream) would lock the tab in the markdown
  // pipeline + layout. Render it as plain, break-anywhere text instead.
  if (isPathologicalText(content)) {
    return <PlainTextGuard className={className} content={content} />;
  }
  return (
    <Suspense fallback={<MarkdownFallback className={className} content={content} />}>
      <RichMarkdownText className={className} content={content} isStreaming={isStreaming} />
    </Suspense>
  );
});

function PlainTextGuard({ className = "", content }: Pick<MarkdownTextProps, "className" | "content">) {
  const { shown, hiddenCount } = clampPlainText(content);
  return (
    <div className={`agent-markdown ${className}`.trim()}>
      <pre className="agent-markdown-plain-guard">
        {shown}
        {hiddenCount > 0 ? `\n… [${hiddenCount.toLocaleString()} more characters not shown]` : ""}
      </pre>
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
