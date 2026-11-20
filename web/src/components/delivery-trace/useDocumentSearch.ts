import { useEffect, useId, useRef, useState, type RefObject } from "react";

/** Browser ranges highlight rendered text without modifying React-owned DOM. */
export function useDocumentSearch(pane: RefObject<HTMLDivElement>, query: string, revision: string) {
  const id = `plan-search-${useId().replace(/[^a-z0-9]/gi, "")}`;
  const ranges = useRef<Range[]>([]);
  const [count, setCount] = useState(0);
  const [active, setActive] = useState(0);
  const reveal = (index: number) => {
    const range = ranges.current[index];
    if (!range) return;
    let el = range.startContainer.parentElement;
    for (let parent = el; parent; parent = parent.parentElement) {
      if (parent instanceof HTMLDetailsElement) parent.open = true;
    }
    el?.scrollIntoView({ block: "center" });
    if (typeof Highlight !== "undefined") CSS.highlights.set(`${id}-active`, new Highlight(range));
  };
  useEffect(() => {
    const root = pane.current;
    if (!root) return;
    setActive(0);
    const scan = () => {
      const needle = query.trim().toLowerCase();
      const found: Range[] = [];
      if (needle) {
        // Search within paragraphs/cells/code, including text split by inline
        // emphasis or syntax tokens, without matching across unrelated blocks.
        const blocks = root.querySelectorAll("p,li,td,th,h1,h2,h3,h4,h5,h6,pre");
        for (const block of blocks) {
          if (block.querySelector("p,li,pre")) continue;
          const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
          const nodes: { node: Node; start: number; end: number }[] = [];
          let text = "";
          for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const start = text.length; text += node.textContent || "";
            nodes.push({ node, start, end: text.length });
          }
          const lower = text.toLowerCase();
          for (let offset = lower.indexOf(needle); offset !== -1; offset = lower.indexOf(needle, offset + needle.length)) {
            const first = nodes.find(n => n.end > offset);
            const last = nodes.find(n => n.end >= offset + needle.length);
            if (!first || !last) continue;
            const range = document.createRange();
            range.setStart(first.node, offset - first.start);
            range.setEnd(last.node, offset + needle.length - last.start);
            found.push(range);
          }
        }
      }
      ranges.current = found; setCount(found.length);
      if (typeof Highlight !== "undefined") {
        CSS.highlights.set(id, new Highlight(...found));
        CSS.highlights.delete(`${id}-active`);
      }
    };
    scan();
    const observer = new MutationObserver(scan);
    observer.observe(root, { childList: true, characterData: true, subtree: true });
    return () => { observer.disconnect(); if (typeof Highlight !== "undefined") { CSS.highlights.delete(id); CSS.highlights.delete(`${id}-active`); } };
  }, [query, revision, id, pane]);
  return { count, active, style: `::highlight(${id}){background:#ffe89a;color:#241c00}::highlight(${id}-active){background:#ffbe61;color:#241c00}`,
    move: (delta: number) => { const next = count ? (active + delta + count) % count : 0; setActive(next); reveal(next); },
    reveal: () => reveal(active),
  };
}
