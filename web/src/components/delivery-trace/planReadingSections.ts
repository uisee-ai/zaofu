import { planDocumentSections } from "./planDocumentSections.js";

// Presentation aliases only: quote the author's sections verbatim. Never turn
// prose into decision state, acceptance verdicts, or new approval questions.
const titles = {
  overview: /^(?:PRD[：:]|审批摘要|本次交付|权威、范围与(?:当前状态|审批入口)|Authority and user outcomes|authority 与产品规格|产品摘要|产品目标|目标与范围|评审入口|overview|executive summary)/i,
  changes: /^(?:本次变更|变更摘要|修订影响|变更与保留|change summary|revision impact)/i,
  journeys: /(?:业务旅程|用户旅程|PRD 用户结果|交互流程|user flow|user journey)/i,
  pages: /(?:低保真|页面.*设计|交互设计|设计交接|wireframe|design handoff)/i,
  decisions: /^(?:decision register|owner 决策项|待决事项|未决事项|决定登记|决策登记|决策、覆盖与交付清单)/i,
};
export type ReadingSection = ReturnType<typeof planDocumentSections>[number];
const sharedTitle = /^(?:Shared Spec|共享 Task Spec)(?:\s*[：:].*)?$/;
export function planReadingSections(markdown: string) {
  const sections = planDocumentSections(markdown);
  const select = (pattern: RegExp) => sections.flatMap((section, index) => {
    if (!pattern.test(section.title)) return [];
    let end = index + 1;
    while (end < sections.length && sections[end].level > section.level) end++;
    return [{ ...section, content: sections.slice(index, end).map(s => s.content).join("") }];
  });
  // A heading's wording cannot prove it is an approval summary. Offer the
  // first chapter only as a labelled quotation, never as inferred authority.
  const firstChapter = sections.findIndex(section => section.level === 2);
  const first = sections[firstChapter];
  let end = firstChapter + 1;
  while (end < sections.length && sections[end].level > 2) end++;
  const opening = first && !/^Task Spec:/.test(first.title) && !sharedTitle.test(first.title)
    ? [{ ...first, content: sections.slice(firstChapter, end).map(section => section.content).join("") }] : [];
  return { opening, changes: select(titles.changes), overview: select(titles.overview), journeys: select(titles.journeys), pages: select(titles.pages), decisions: select(titles.decisions) };
}

/** Exact author-owned Task IDs only; headings inside fences are excluded by the AST. */
export function planTaskSpecifications(markdown: string, taskIds: string[]) {
  const sections = planDocumentSections(markdown);
  const tasks: Record<string, ReadingSection> = Object.create(null);
  const warnings: string[] = [];
  const candidates = new Map<string, ReadingSection[]>();
  const shared: ReadingSection[] = [];
  sections.forEach((section, index) => {
    if (section.level !== 2) return;
    const match = section.title.match(/^Task Spec:\s*(\S+)\s*$/);
    if (!match && !sharedTitle.test(section.title)) return;
    let end = index + 1;
    while (end < sections.length && sections[end].level > section.level) end++;
    const excerpt = { ...section, content: sections.slice(index, end).map(s => s.content).join("") };
    if (!match) { shared.push(excerpt); return; }
    const id = match[1];
    if (!taskIds.includes(id)) { warnings.push(`原文任务规格引用未知任务：${id}`); return; }
    candidates.set(id, [...(candidates.get(id) || []), excerpt]);
  });
  candidates.forEach((values, id) => {
    if (values.length === 1) tasks[id] = values[0];
    else warnings.push(`${id} 存在重复原文任务规格，请在完整原文中核对。`);
  });
  // Fold identical authored sections (ignoring edge whitespace) across tasks. Keep
  // every original segment, its order and the complete document unchanged.
  const pieces = Object.fromEntries(Object.entries(tasks).map(([id, section]) => [id, planDocumentSections(section.content)]));
  const consumers = new Map<string, Set<string>>();
  Object.entries(pieces).forEach(([id, parts]) => parts.filter(p => p.level > 2).forEach(p => {
    const key = p.content.trim();
    consumers.set(key, new Set([...(consumers.get(key) || []), id]));
  }));
  const reading = Object.fromEntries(Object.entries(pieces).map(([id, parts]) => [id, parts.map(part => ({
    ...part, shared: part.level > 2 && (consumers.get(part.content.trim())?.size || 0) > 1,
  }))]));
  // Unassigned siblings may hold essential setup/restore details. Expose their
  // authored text without guessing which task owns it or extending task scope.
  const unassigned = sections.flatMap((section, index) => {
    if (section.level !== 2 || sharedTitle.test(section.title) || /^Task Spec:/.test(section.title)) return [];
    let end = index + 1;
    while (end < sections.length && sections[end].level > section.level) end++;
    return [{ ...section, content: sections.slice(index, end).map(s => s.content).join("") }];
  });
  return { tasks, shared, warnings, reading, unassigned };
}
