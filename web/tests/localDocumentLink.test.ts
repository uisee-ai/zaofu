import { localDocumentLink } from "../src/components/agent-session/previewRegistry.js";
import { synthesisRequestPayload } from "../src/components/channel/channelControlActions.js";
import { fromMarkdown } from "mdast-util-from-markdown";
import { localDocumentMarkdownRef, remarkLocalDocuments } from "../src/components/agent-session/localDocumentMarkdown.js";
function assert(ok: unknown, message: string) { if (!ok) throw new Error(message); }
for (const path of ["/home/min/project/PRD.md", "docs/PRD.md", "docs/a b.md", "outputs/data.json"]) {
  assert(localDocumentLink(path)?.ref.path === path, "local path must stay an exact bounded preview ref");
}
for (const href of ["https://site/PRD.md", "//host/PRD.md", "file:///tmp/PRD.md", "javascript:alert(1)", "#part", "/?page=channels", "docs/a.md?raw=1"]) {
  assert(localDocumentLink(href) === null, "URLs and app routes are not local file references");
}
for (const ref of ["docs/PRD.md", "./docs/PRD.md", "/home/狼人杀/docs/PRD.md", "docs/需求 文档.md", "docs/100%20完成.md"]) {
  const tree = fromMarkdown(`[完整文档](<${ref}>)`);
  remarkLocalDocuments()(tree);
  const paragraph = tree.children[0];
  const link = paragraph.type === "paragraph" ? paragraph.children[0] : undefined;
  assert(link?.type === "link" && localDocumentMarkdownRef(link.url) === ref,
    "Markdown parsing must preserve the exact file ref, including Unicode, spaces and literal percent escapes");
}
for (const href of ["https://site/PRD.md", "#part", "#zf-local-document=%ZZ", "#zf-local-document=javascript%3Aalert(1)"]) {
  assert(localDocumentMarkdownRef(href) === "", "only valid local document transports can open a preview");
}
const external = fromMarkdown("[site](https://site/PRD.md)");
const before = JSON.stringify(external);
remarkLocalDocuments()(external);
assert(JSON.stringify(external) === before, "external links retain the existing sanitizer and hardener pipeline");
const source = { ref: "docs/PRD.md", sha256: "digest" };
assert(synthesisRequestPayload("ch", "thread", undefined, source).source_file === source, "publish binds the exact preview selection");
assert(!("source_file" in synthesisRequestPayload("ch", "thread")), "automatic/ordinary synthesis payload unchanged");
console.log("localDocumentLink tests passed");
