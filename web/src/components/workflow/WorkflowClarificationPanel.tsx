import { Check, FileDiff } from "lucide-react";
import { useEffect, useState } from "react";

import { asRecordArray, asStringArray, textValue } from "../../app/shared";
import {
  AskUserQuestion,
  type AskUserQuestionAnswer,
} from "../common/AskUserQuestion";
import { StatusBadge } from "./WorkflowProposalParts";

export function WorkflowClarificationPanel({
  actionReady,
  actionState,
  busyAction,
  intakeRef,
  onClarify,
  onPrepare,
  requestKind,
  requestId,
  requestStatus,
  requirement,
  requirementDigest,
}: {
  actionReady: boolean;
  actionState: string;
  busyAction: string;
  intakeRef: string;
  onClarify: (payload: Record<string, unknown>) => void;
  onPrepare: (payload: Record<string, unknown>) => void;
  requestKind: string;
  requestId: string;
  requestStatus: string;
  requirement: Record<string, unknown>;
  requirementDigest: string;
}) {
  const [objective, setObjective] = useState(textValue(requirement.objective));
  const [sourceRoot, setSourceRoot] = useState(textValue(requirement.source_root));
  const [targetRoot, setTargetRoot] = useState(textValue(requirement.target_root));
  const [acceptance, setAcceptance] = useState(asStringArray(requirement.acceptance).join("\n"));
  const [constraints, setConstraints] = useState(asStringArray(requirement.constraints).join("\n"));
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    setObjective(textValue(requirement.objective));
    setSourceRoot(textValue(requirement.source_root));
    setTargetRoot(textValue(requirement.target_root));
    setAcceptance(asStringArray(requirement.acceptance).join("\n"));
    setConstraints(asStringArray(requirement.constraints).join("\n"));
    setEditing(false);
  }, [requestId, requirement.revision]);
  const openQuestions = asStringArray(requirement.open_questions);
  const questionBatch = openQuestions.slice(0, 3);
  const remainingOpenQuestions = openQuestions.slice(3);
  const clarificationAnswerCount = asRecordArray(
    requirement.clarification_answers,
  ).length;
  const requirementRevision = Number(requirement.revision || 0);
  const preparePayload = {
    request_id: requestId,
    intake_ref: intakeRef,
    kind: requestKind,
    allow_missing_env: true,
    expected_revision: requirementRevision,
    expected_requirement_digest: requirementDigest,
  };

  if (requestStatus === "ready" && !editing) {
    return (
      <section className="workflow-clarification compact" data-testid="workflow-ready-to-prepare">
        <div className="workflow-clarification-head">
          <div>
            <span className="eyebrow">Requirement revision</span>
            <strong>Confirmed and ready</strong>
          </div>
          <button type="button" disabled={!actionReady || busyAction !== ""}
            onClick={() => setEditing(true)}>修改需求与配置约束</button>
          <button
            className="icon-button primary"
            disabled={!actionReady || busyAction !== "" || !intakeRef}
            title={!actionReady ? actionState : "Build the exact proposal for this revision"}
            type="button"
            onClick={() => onPrepare(preparePayload)}
          >
            <FileDiff aria-hidden="true" size={16} />
            {busyAction === "workflow-prepare" ? "Preparing" : "Prepare proposal"}
          </button>
        </div>
      </section>
    );
  }
  if (!["clarifying", "draft", "ready"].includes(requestStatus)) return null;

  const clarificationPayload = {
    ...preparePayload,
    objective,
    source_root: sourceRoot,
    target_root: targetRoot,
    acceptance: multilineValues(acceptance),
    constraints: multilineValues(constraints),
    requested_by: "web",
  };
  return (
    <section className="workflow-clarification" data-testid="workflow-clarification">
      <div className="workflow-clarification-head">
        <div>
          <span className="eyebrow">Requirement revision</span>
          <strong>Clarify request</strong>
        </div>
        <StatusBadge status={requestStatus} />
      </div>
      <label>
        <span>Objective</span>
        <textarea
          aria-label="Objective"
          rows={3}
          value={objective}
          onChange={(event) => setObjective(event.target.value)}
        />
      </label>
      <div className="workflow-clarification-paths">
        {requestKind === "refactor" ? (
          <label>
            <span>Source root</span>
            <input
              aria-label="Source root"
              value={sourceRoot}
              onChange={(event) => setSourceRoot(event.target.value)}
            />
          </label>
        ) : null}
        {["prd", "refactor"].includes(requestKind) ? (
          <label>
            <span>Target root</span>
            <input
              aria-label="Target root"
              value={targetRoot}
              onChange={(event) => setTargetRoot(event.target.value)}
            />
          </label>
        ) : null}
      </div>
      <div className="workflow-clarification-columns">
        <label>
          <span>Acceptance</span>
          <textarea
            aria-label="Acceptance"
            rows={4}
            value={acceptance}
            onChange={(event) => setAcceptance(event.target.value)}
          />
        </label>
        <label>
          <span>Constraints</span>
          <textarea
            aria-label="Constraints"
            rows={4}
            value={constraints}
            onChange={(event) => setConstraints(event.target.value)}
          />
        </label>
      </div>
      {questionBatch.length ? (
        <AskUserQuestion
          busy={busyAction !== ""}
          disabled={!actionReady || !objective.trim()}
          onSubmit={(answers: AskUserQuestionAnswer[]) => onClarify({
            ...clarificationPayload,
            open_questions: remainingOpenQuestions,
            clarification_answers: answers
              .filter((answer) => !answer.skipped)
              .map((answer) => ({
                question: questionBatch.find((_, index) => (
                  `workflow-question-${index + 1}` === answer.questionId
                )) || "",
                answer: answer.answer,
              })),
            confirm: remainingOpenQuestions.length === 0,
          })}
          questions={questionBatch.map((question, index) => ({
            id: `workflow-question-${index + 1}`,
            header: "Clarification",
            question,
            inputPlaceholder: "Type a concrete answer.",
            required: true,
          }))}
          requestId={[
            requestId,
            String(requirement.revision || ""),
            openQuestions.join("|"),
          ].join(":")}
          submitLabel={remainingOpenQuestions.length
            ? "Save answers"
            : "Save & prepare proposal"}
        />
      ) : (
        <div className="workflow-clarification-actions">
          {clarificationAnswerCount ? (
            <span>{clarificationAnswerCount} decisions recorded</span>
          ) : null}
          <button
            className="icon-button primary"
            data-testid="workflow-save-prepare-proposal"
            disabled={!actionReady || busyAction !== "" || !objective.trim()}
            title={!actionReady ? actionState : "Save the revision and prepare its proposal"}
            type="button"
            onClick={() => onClarify({
              ...clarificationPayload,
              open_questions: [],
              confirm: true,
            })}
          >
            <Check aria-hidden="true" size={16} />
            {busyAction === "workflow-clarify" || busyAction === "workflow-prepare"
              ? "Preparing"
              : "Save & prepare proposal"}
          </button>
        </div>
      )}
    </section>
  );
}

function multilineValues(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}
