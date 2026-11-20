import { useEffect, useId, useState } from "react";

// Mermaid is bundled locally. Never send document text to a rendering service
// or load a remote renderer. The SVG frame cannot execute scripts or requests.
export default function MarkdownDiagram({ code }: { code: string }) {
  const id = `diagram-${useId().replace(/[^a-z0-9]/gi, "")}`;
  const [result, setResult] = useState<{ code: string; svg?: string; error?: string }>({ code });
  useEffect(() => {
    let cancelled = false;
    void import("mermaid").then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", suppressErrorRendering: true, maxTextSize: 50000,
        flowchart: { htmlLabels: false }, secure: ["securityLevel", "startOnLoad", "maxTextSize", "suppressErrorRendering", "htmlLabels"] });
      const { svg } = await mermaid.render(id, code);
      if (!cancelled) setResult({ code, svg });
    }).catch(error => { if (!cancelled) setResult({ code, error: error instanceof Error && error.message.includes("Unsupported diagram family")
      ? "该图类型暂不支持图形预览，请查看源码；可使用 flowchart、sequenceDiagram 或 C4 表达。"
      : "流程图无法渲染，请查看源码检查图语法。" }); });
    return () => { cancelled = true; };
  }, [code, id]);
  if (result.code !== code || (!result.svg && !result.error)) return <p role="status">正在绘制流程图…</p>;
  if (result.error) return <p role="status">{result.error}</p>;
  // A sandboxed srcdoc is a separate security boundary even if a future
  // renderer accepts external SVG images, links, or unsafe diagram directives.
  const document = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"><style>body{margin:0;background:white}svg{max-width:100%;height:auto;display:block;margin:auto}</style></head><body>${result.svg}</body></html>`;
  return <iframe className="agent-diagram-frame" title="Mermaid 流程图" sandbox="" srcDoc={document} />;
}
