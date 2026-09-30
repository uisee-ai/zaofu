import { issueStateDescription, issueStateLabel, issueStateStage } from "./issueTriageModel";
import type { CSSProperties } from "react";
import { IssueIcon, type IssueIconName } from "./IssueIcon";
export type IssueStage = "repo" | "triage" | "decision" | "fix" | "delivery" | "complete" | "stopped";
const stageIcons: Record<IssueStage, IssueIconName> = {
    repo: "clock", triage: "dot", decision: "warn", fix: "wrench",
    delivery: "pr", complete: "shield", stopped: "xcircle",
};
/** Presentation only. Derive the effective state and stage in the real-data adapter. */
export function IssueStateBadge({ label, stage, tooltip, className = "" }: {
    label: string;
    stage: IssueStage;
    tooltip?: string;
    className?: string;
}) {
    const style = { "--stage": `var(--${stage})`, "--soft": `var(--${stage}-soft)` } as CSSProperties;
    return <span className={`group-badge ${className}`.trim()} data-tooltip={tooltip} style={style}>
    <IssueIcon name={stageIcons[stage]} size="sm"/>{label}
  </span>;
}
export function IssueStateValue({ state }: {
    state: string;
}) {
    const group = issueStateStage(state).id;
    return <IssueStateBadge label={issueStateLabel(state)} stage={group === 'repository' ? 'repo' : group} tooltip={issueStateDescription(state)}/>;
}
