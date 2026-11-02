export type PrdAudienceView = "product" | "architecture" | "developer";

export interface PrdViewIndex {
  label: string;
  sections: string[];
}

type Heading = { level: number; title: string; start: number };

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
