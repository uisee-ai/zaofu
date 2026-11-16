export type PrdAudienceView = "product" | "architecture" | "developer";

export interface PrdViewIndex {
  label: string;
  sections: string[];
}

type Heading = { level: number; title: string; start: number };

/** Fold only named audit sections, never arbitrary risks/decisions or text
 * matched by path/hash keywords. Slices concatenate to the exact input. */
export function prdReadingSections(markdown: string): Array<{ content: string; auditTitle?: string }> {
  const headings = markdownHeadings(markdown);
  const result: Array<{ content: string; auditTitle?: string }> = [];
  let cursor = 0;
  let parent = "";
  for (let index = 0; index < headings.length; index += 1) {
    const heading = headings[index];
    if (heading.level === 2) parent = heading.title;
    const audit = heading.level === 2 && heading.title === "Provenance"
      || heading.level === 3 && parent === "需求追溯与决策记录" && heading.title === "来源覆盖";
    if (!audit || heading.start < cursor) continue;
    const next = headings.slice(index + 1).find(item => item.level <= heading.level);
    if (heading.start > cursor) result.push({ content: markdown.slice(cursor, heading.start) });
    cursor = next?.start ?? markdown.length;
    result.push({ content: markdown.slice(heading.start, cursor), auditTitle: heading.title === "Provenance" ? "文档来源" : "来源覆盖明细" });
  }
  if (cursor < markdown.length) result.push({ content: markdown.slice(cursor) });
  return result.length ? result : [{ content: markdown }];
}

/** Filter the same PRD revision into a read-only audience view without duplicating its body. */
export function prdMarkdownView(
  markdown: string,
  view: PrdAudienceView,
  index?: Partial<Record<PrdAudienceView, PrdViewIndex>>,
): string {
  if (!markdown || !index?.[view]?.sections?.length) return markdown;
  const headings = markdownHeadings(markdown);
  const sections = headings.filter((heading) => heading.level === 2);
  if (!sections.length) return markdown;
  const allowed = new Set(index[view]!.sections.map(normalizeHeading));
  const selected = sections.filter((section) => allowed.has(normalizeHeading(section.title)));
  if (!selected.length) return markdown;
  const preamble = markdown.slice(0, sections[0].start);
  const body = selected.map((section) => {
    const position = sections.indexOf(section);
    const next = sections[position + 1];
    const end = next ? next.start : markdown.length;
    return markdown.slice(section.start, end);
  }).join("");
  return preamble + body;
}

function markdownHeadings(markdown: string): Heading[] {
  const headings: Heading[] = [];
  let offset = 0;
  let fence = "";
  for (const line of markdown.split(/(?<=\n)/)) {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})(.*)/);
    if (fence) {
      if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = "";
    } else if (marker) {
      fence = marker[1];
    } else {
      const match = line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*(?:\r?\n|$)/);
      if (match) headings.push({ level: match[1].length, title: match[2].trim(), start: offset });
    }
    offset += line.length;
  }
  return headings;
}

function normalizeHeading(value: string): string {
  return value.trim().toLowerCase().replace(/^\d{1,2}\s*[.)-]\s*/, "").replace(/&/g, " and ").replace(/[^\w\u4e00-\u9fff]+/g, " ").trim();
}
