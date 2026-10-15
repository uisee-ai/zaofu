import { localDocumentLink } from "../src/components/agent-session/previewRegistry.js";
import { synthesisRequestPayload } from "../src/components/channel/channelControlActions.js";
function assert(ok: unknown, message: string) { if (!ok) throw new Error(message); }
for (const path of ["/home/min/project/PRD.md", "docs/PRD.md", "docs/a b.md", "outputs/data.json"]) {
  assert(localDocumentLink(path)?.ref.path === path, "local path must stay an exact bounded preview ref");
}
for (const href of ["https://site/PRD.md", "//host/PRD.md", "file:///tmp/PRD.md", "javascript:alert(1)", "#part", "/?page=channels", "docs/a.md?raw=1"]) {
  assert(localDocumentLink(href) === null, "URLs and app routes are not local file references");
}
const source = { ref: "docs/PRD.md", sha256: "digest" };
assert(synthesisRequestPayload("ch", "thread", undefined, source).source_file === source, "publish binds the exact preview selection");
assert(!("source_file" in synthesisRequestPayload("ch", "thread")), "automatic/ordinary synthesis payload unchanged");
console.log("localDocumentLink tests passed");
