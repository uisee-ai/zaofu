import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import {
  ChevronRight,
  GitFork,
  Info,
  type LucideIcon,
} from "lucide-react";
import { Fragment } from "react";

import { textValue } from "../../app/shared";
import {
  groupDiagnostics,
  stageRoleLabel,
  type DiagnosticGroup,
  type WorkflowRequest,
} from "./workflowProposalModel";

export function WorkflowExecutionPlan({
  explicitDag,
  levels,
  stages,
}: {
  explicitDag: boolean;
  levels: WorkflowRequest[][];
  stages: WorkflowRequest[];
}) {
  useSettingsPreferences();
  if (!stages.length) {
    return <span className="muted">No declared stages for this flow.</span>;
  }
  if (!explicitDag) {
    return (
      <div
        className="workflow-event-stage-grid"
        data-testid="workflow-proposal-graph"
      >
        {stages.map((stage, index) => (
          <WorkflowStage key={`${textValue(stage.id)}-${index}`} stage={stage} />
        ))}
      </div>
    );
  }
  return (
    <div className="workflow-plan-dag" data-testid="workflow-proposal-graph">
      {levels.map((level, levelIndex) => (
        <Fragment key={`level-${levelIndex}`}>
          <div className="workflow-plan-level">
            <small>{uiText("Phase")} {levelIndex + 1}</small>
            {level.map((stage, stageIndex) => (
              <WorkflowStage
                key={`${textValue(stage.id)}-${stageIndex}`}
                stage={stage}
              />
            ))}
          </div>
          {levelIndex < levels.length - 1 ? (
            <ChevronRight
              aria-hidden="true"
              className="workflow-plan-connector"
              size={18}
            />
          ) : null}
        </Fragment>
      ))}
    </div>
  );
}

function WorkflowStage({ stage }: { stage: WorkflowRequest }) {
  const trigger = textValue(stage.trigger);
  return (
    <div className="workflow-plan-stage">
      <strong>{textValue(stage.id) || "unnamed-stage"}</strong>
      <span>{stageRoleLabel(stage)}</span>
      {trigger ? <small title={trigger}>{trigger}</small> : null}
    </div>
  );
}

export function WorkflowDiagnostics({
  blockers,
  info,
  warnings,
}: {
  blockers: WorkflowRequest[];
  info: WorkflowRequest[];
  warnings: WorkflowRequest[];
}) {
  const blockerGroups = groupDiagnostics(blockers, "STOP");
  const warningGroups = groupDiagnostics(warnings, "WARN");
  const infoGroups = groupDiagnostics(info, "INFO");
  return (
    <div className="workflow-diagnostic-summary">
      {blockerGroups.map((group) => (
        <DiagnosticRow group={group} key={`stop-${group.key}`} />
      ))}
      {warningGroups.map((group) => (
        <DiagnosticRow group={group} key={`warn-${group.key}`} />
      ))}
      {infoGroups.length ? (
        <details className="workflow-diagnostic-info">
          <summary>
            <Info aria-hidden="true" size={14} />
            {info.length} informational notes
          </summary>
          <div>
            {infoGroups.map((group) => (
              <DiagnosticRow group={group} key={`info-${group.key}`} />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function DiagnosticRow({ group }: { group: DiagnosticGroup }) {
  return (
    <div className={`workflow-diagnostic-row tone-${group.severity.toLowerCase()}`}>
      <span>{group.severity}</span>
      <div>
        <strong>{group.kind}</strong>
        <small>{group.message}</small>
      </div>
      {group.count > 1 ? <code>{group.count}x</code> : null}
    </div>
  );
}

export function SectionHeading({
  icon: Icon,
  meta,
  title,
}: {
  icon: LucideIcon;
  meta: string;
  title: string;
}) {
  return (
    <div className="workflow-proposal-section-head">
      <span><Icon aria-hidden="true" size={15} /><strong>{title}</strong></span>
      <small>{meta}</small>
    </div>
  );
}

export function WorkflowList({ items, title }: { items: string[]; title: string }) {
  useSettingsPreferences();
  return (
    <div>
      <strong>{title}</strong>
      {items.length ? (
        <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
      ) : <span className="muted">{uiText("None")}</span>}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const normalized = status || "unknown";
  const tone = (
    ["running", "submitted", "approved"].includes(normalized)
      ? "info"
      : ["proposed", "ready", "draft", "queued", "paused"].includes(normalized)
        ? "warn"
        : normalized === "rejected"
          ? "err"
          : "muted"
  );
  return <span className={`badge badge-${tone}`}>{normalized}</span>;
}

export function WorkflowEmpty({ title }: { title: string }) {
  return (
    <div className="workflow-proposal-empty">
      <GitFork aria-hidden="true" size={24} />
      <strong>{title}</strong>
    </div>
  );
}
