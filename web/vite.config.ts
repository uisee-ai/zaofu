import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.ZF_API_TARGET ?? "http://127.0.0.1:8001";

function optionalFeatureChunk(id: string): string | undefined {
  const path = id.replaceAll("\\", "/");
  // Keep optional diagram vendor families out of the main reading bundle.
  if (path.includes("/lodash-es/")) return "diagram-collections";
  if (path.includes("/cytoscape/")) return "diagram-cytoscape";
  if (/\/node_modules\/(?:d3(?:-[^/]+)?|dagre-d3-es)\//.test(path)) return "diagram-layout";
  if (path.includes("/khroma/")) return "diagram-colors";
  if (path.includes("/dompurify/")) return "diagram-sanitize";
  if (/\/node_modules\/(?:roughjs|@iconify\/utils|@braintree\/sanitize-url|marked|dayjs|stylis|es-toolkit|fastdom|uuid|ts-dedent)\//.test(path)) return "diagram-utils";
  if (path.includes("/@emoji-mart/data/")) return "emoji-data";
  if (
    path.includes("/@emoji-mart/react/")
    || path.includes("/emoji-mart/")
  ) {
    return "emoji-picker";
  }

  const language = path.match(
    /\/@shikijs\/langs\/dist\/([^/]+)\.mjs$/,
  )?.[1];
  if (language) {
    if (["javascript", "typescript"].includes(language)) return "shiki-lang-js";
    if (["jsx", "tsx"].includes(language)) return "shiki-lang-jsx";
    if (["css", "html", "json", "markdown"].includes(language)) {
      return "shiki-lang-web";
    }
    if (["bash", "shellscript", "toml", "yaml"].includes(language)) {
      return "shiki-lang-shell";
    }
    return "shiki-lang-other";
  }
  if (path.includes("/@shikijs/engine-oniguruma/")) return "shiki-engine";
  if (
    path.includes("/@shikijs/")
    || path.includes("/shiki/")
    || path.includes("/vscode-textmate/")
  ) {
    return "shiki-core";
  }
  if (
    path.includes("/katex/")
    || path.includes("/rehype-katex/")
    || path.includes("/remark-math/")
    || path.includes("/mdast-util-math/")
    || path.includes("/micromark-extension-math/")
  ) {
    return "markdown-math";
  }
  return undefined;
}

export default defineConfig({
  plugins: [react(), {
    // Flowchart, sequence, class, state, ER and C4 use Mermaid's built-in
    // parsers. New Langium diagram families carry a >500k monolithic parser;
    // those retain their source with an explicit unsupported-type fallback.
    name: "bounded-diagram-families",
    enforce: "pre",
    transform(code, id) {
      if (id.replaceAll("\\", "/").includes("/mermaid/dist/chunks/mermaid.core/")
        && /from ["']@mermaid-js\/parser["']/.test(code)) {
        return { code: 'throw new Error("Unsupported diagram family; use source view"); export const diagram = {};', map: null };
      }
    },
  }],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": apiTarget,
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: optionalFeatureChunk,
        onlyExplicitManualChunks: true,
      },
    },
  },
});
