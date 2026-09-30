import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.ZF_API_TARGET ?? "http://127.0.0.1:8001";

function optionalFeatureChunk(id: string): string | undefined {
  const path = id.replaceAll("\\", "/");
  // Keep the shell entry under the enforced budget. Board workbench is a
  // substantial view that is only rendered on the board page; emitting it as
  // its own ESM chunk does not change its static-import behaviour.
  if (path.endsWith("/components/kanban/BoardWorkbench.tsx")) return "board-workbench";
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
  plugins: [react()],
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
      },
    },
  },
});
