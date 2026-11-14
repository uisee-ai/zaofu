import { documentReferences } from "../src/components/agent-session/documentReferences.js";
import { fromMarkdown } from "mdast-util-from-markdown";
const assert = { equal(actual: unknown, expected: unknown) { if (actual !== expected) throw new Error(`${JSON.stringify(actual)} !== ${JSON.stringify(expected)}`); } };

const source = '# Links\n\n[Read][SPEC]\n\n## Definitions\n\n[spec]: https://example.invalid/first "a & \\"quoted\\" title"\n\n[spec]: https://example.invalid/second\n\n> [nested]: https://example.invalid/nested\n\n```md\n[code]: https://example.invalid/ignored\n```\n';
const definitions = documentReferences([source]);
const tree = fromMarkdown(definitions + '\n\n[Read][SPEC] and [nested]\n');
assert.equal(tree.children.filter(n => n.type === 'definition').length, 2);
assert.equal(tree.children[0].type, 'definition');
if (tree.children[0].type === 'definition') {
  assert.equal(tree.children[0].url, 'https://example.invalid/first');
  assert.equal(tree.children[0].title, 'a & "quoted" title');
}
assert.equal(documentReferences(['```md\n[code]: /not-a-reference\n```']), '');
assert.equal(documentReferences(['no references']), '');
console.log('documentReferences.test.ts OK');

assert.equal(documentReferences(["x".repeat(5001) + "\n\n[skip]: /oversized-token"]), "");
assert.equal(documentReferences(["# A\n\n[Read][ref]", "# B\n\n[ref]: /target"]), "[ref]: </target>");
