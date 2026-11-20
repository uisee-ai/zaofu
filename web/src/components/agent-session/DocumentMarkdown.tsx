import { MarkdownText } from "./MarkdownText";
import { isPathologicalText } from "./markdownGuard";
import "./documentMarkdown.css";

/** Shared reading surface; generated context never changes the saved source. */
export function DocumentMarkdown({ content, className = "", definitions = "" }: { content: string; className?: string; definitions?: string }) {
  const rendered = definitions ? `${definitions}\n\n${content}` : content;
  return <div className="document-reading-surface">
    {definitions && isPathologicalText(rendered)
      ? <><p role="status">引用上下文过长，本节按原文显示；可下载完整文档。</p>{isPathologicalText(content) ? <MarkdownText content={content} className={className} /> : <pre className="agent-markdown-plain-guard">{content}</pre>}</>
      : <MarkdownText content={rendered} className={`document-markdown ${className}`} />}
  </div>;
}
