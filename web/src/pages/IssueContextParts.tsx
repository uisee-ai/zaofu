import type { ReactNode } from "react";
import { IssueIcon } from "./IssueIcon";
export function IssueContextFacts({ entries }: {
    entries: Array<[
        string,
        ReactNode,
        boolean?
    ]>;
}) {
    return <dl className="context-facts">{entries.map(([label, raw, code]) => {
            const value = raw == null || raw === '' ? '—' : raw;
            return <div key={label}><dt>{label}</dt><dd>{code ? <code>{value}</code> : value}</dd></div>;
        })}</dl>;
}
export function IssueProgress({ label, percent }: {
    label: string;
    percent?: number;
}) {
    const known = typeof percent === 'number' && Number.isFinite(percent) && percent >= 0 && percent <= 100;
    return <div className="context-progress"><div className="context-progress-head"><span>{label}</span><strong>{known ? `${percent}%` : 'In progress'}</strong></div><div className={`context-progress-track ${known ? '' : 'indeterminate'}`} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={known ? percent : undefined}><span style={known ? { width: `${percent}%` } : undefined}/></div></div>;
}
export function IssueEvidenceList({ entries }: {
    entries: Array<{
        text: string;
        passed?: boolean;
    }>;
}) {
    return <ul className="context-list">{entries.map(({ text, passed }, i) => <li key={`${i}:${text}`}><IssueIcon name={passed ? 'check' : 'warn'}/>{text}</li>)}</ul>;
}
