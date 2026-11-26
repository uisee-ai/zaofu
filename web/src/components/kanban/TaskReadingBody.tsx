import { asRecord } from "../../app/shared";
import { MarkdownText } from "../agent-session/MarkdownText";
import { isPathologicalText } from "../agent-session/markdownGuard";
import "./taskReadingBody.css";

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function prose(source: string): string {
  // Historical contracts can contain several Chinese sentences in one line.
  // Leave authored Markdown, URLs and explicit line breaks intact.
  if (source.length < 140 || isPathologicalText(source)) return source;
  if (/[\r\n`*\[\]<>|]|:\/\/|^\s*(?:[#{>]|[-+]\s|\d+[.)]\s)|(?:^|\s)_\S.*?_/.test(source)) return source;
  return source.replace(/([。！？]+[”’」』]*)(?=[\p{Letter}\p{Number}“‘「『])/gu, "$1\n\n");
}

function criterion(item: unknown): string {
  if (typeof item === "string") return text(item);
  const row = asRecord(item);
  return [row.text, row.statement, row.criterion, row.description, row.acceptance].map(text).find(Boolean) ?? "";
}

export function TaskReadingBody({ contract, title, objective }: {
  contract: Record<string, unknown>; title?: string; objective?: unknown;
}) {
  const description = text(contract.behavior) || text(objective);
  const acceptance = text(contract.acceptance);
  const raw = contract.acceptance_criteria;
  const criteria = [...new Set((Array.isArray(raw) ? raw : [raw]).map(criterion).filter(Boolean))];
  const intro = acceptance !== "exit_code=0" && !criteria.includes(acceptance) ? acceptance : "";
  const outputs = Array.isArray(contract.handoff_artifacts)
    ? [...new Set(contract.handoff_artifacts.map(text).filter(Boolean))]
    : [];

  return <>
    {description && description !== title &&
      <div className="reader-description reader-contract-text"><MarkdownText content={prose(description)} /></div>}
    {(intro || criteria.length > 0) && <section className="reader-contract-text">
      <h2>验收条件</h2>
      {intro && <MarkdownText content={prose(intro)} />}
      {criteria.length > 0 && <ul className="reader-criteria">{criteria.map(label =>
        <li key={label}><MarkdownText content={label} /></li>)}</ul>}
    </section>}
    {outputs.length > 0 && <section className="reader-contract-text">
      <h2>约定产物</h2>
      <ul className="reader-output-names">{outputs.map(name => <li key={name}>{name}</li>)}</ul>
    </section>}
  </>;
}
