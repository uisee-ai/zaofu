// Map a tool name to a category icon, so a tool row reads at a glance
// Falls back to a generic wrench. Pure
// lookup — the transient run states (running/error/cancelled) are handled
// by the caller and take priority over the category icon.

import {
  Download,
  FileText,
  FlaskConical,
  FolderTree,
  Globe,
  Pencil,
  Search,
  SquareTerminal,
  Wrench,
  type LucideIcon,
} from "lucide-react";

const RULES: Array<[RegExp, LucideIcon]> = [
  [/^(bash|shell|sh|exec|run|command|terminal)/i, SquareTerminal],
  [/(write|edit|patch|apply|create|update)/i, Pencil],
  [/(read|cat|view|open|get)/i, FileText],
  [/(grep|search|find|rg|ripgrep|glob)/i, Search],
  [/(web|fetch|http|browse|url)/i, Globe],
  [/(test|pytest|jest|spec|check)/i, FlaskConical],
  [/(ls|list|tree|dir|files?)/i, FolderTree],
  [/(download|pull|clone)/i, Download],
];

export function iconForToolName(name: string | undefined): LucideIcon {
  const key = (name ?? "").trim();
  if (key) {
    for (const [pattern, icon] of RULES) {
      if (pattern.test(key)) return icon;
    }
  }
  return Wrench;
}

/**
 * Strip a redundant leading "Tool " from a part title so the row reads
 * verb-first. "Tool bash" → "bash".
 */
export function cleanToolTitle(title: string): string {
  return title.replace(/^tool\s+/i, "").trim() || title;
}
