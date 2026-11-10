import { planDocumentSections } from "./planDocumentSections.js";

// Presentation aliases only: quote the author's sections verbatim. Never turn
// prose into decision state, acceptance verdicts, or new approval questions.
const titles = {
  overview: /^(?:PRD[：:]|产品摘要|产品目标|目标与范围|评审入口|overview|executive summary)/i,
  journeys: /(?:业务旅程|用户旅程|交互流程|user flow|user journey)/i,
  pages: /(?:低保真|页面.*设计|交互设计|设计交接|wireframe|design handoff)/i,
  decisions: /^(?:decision register|owner 决策项|待决事项|决定登记|决策登记)/i,
};
export type ReadingSection = ReturnType<typeof planDocumentSections>[number];
export function planReadingSections(markdown: string) {
  const sections = planDocumentSections(markdown);
  const select = (pattern: RegExp) => sections.flatMap((section, index) => {
    if (!pattern.test(section.title)) return [];
    let end = index + 1;
    while (end < sections.length && sections[end].level > section.level) end++;
    return [{ ...section, content: sections.slice(index, end).map(s => s.content).join("") }];
  });
  return { overview: select(titles.overview), journeys: select(titles.journeys), pages: select(titles.pages), decisions: select(titles.decisions) };
}
