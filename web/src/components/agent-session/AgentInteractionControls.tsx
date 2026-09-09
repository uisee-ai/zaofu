import { Check, PencilLine, X } from "lucide-react";
import {
  AskUserQuestion,
  type AskUserQuestionAnswer,
} from "../common/AskUserQuestion";
import type {
  AgentSessionActionProposal,
  AgentSessionPlanOption,
  AgentSessionPlanQuestion,
  AgentSessionPlanRequest,
  AgentSessionPlanResponse,
} from "./types";
import { actionPresentation } from "./actionPresentation";

function planOptionDetails(option?: AgentSessionPlanOption): string {
  const details = option?.submitDetails;
  if (!details) return "";
  const workflowDetails = details.routeId ? [
    [details.family, details.topology]
      .filter(Boolean)
      .map((value) => value?.replaceAll("_", " "))
      .join(" / "),
    details.laneCount
      ? `${details.laneCount} lane${details.laneCount === 1 ? "" : "s"}`
      : "",
    (details.roles || []).map((role) => role.replaceAll("_", " ")).join(", "),
    details.writerRoles?.length
      ? `Write: ${details.writerRoles.map((role) => role.replaceAll("_", " ")).join(", ")}`
      : "",
    details.verifyRoles?.length
      ? `Verify: ${details.verifyRoles.map((role) => role.replaceAll("_", " ")).join(", ")}`
      : "",
    details.inputKeys?.length
      ? `Inputs: ${details.inputKeys.join(", ")}`
      : "",
    details.preflight ? `Preflight: ${details.preflight}` : "",
    details.outputProfile
      ? `Output: ${details.outputProfile.replaceAll("_", " ")}`
      : "",
  ] : [];
  const routingDetail = details.mode === "multi_lens"
    ? `Blind first pass: ${details.firstPassReplyCount || details.memberCount || 0}-way fanout`
    : details.mode === "clarification"
      ? "Initial routing: 1 facilitator"
      : details.mode === "conversation"
        ? "Initial routing: 1 responder"
        : "";
  const channelDetails = details.templateId ? [
    details.templateName || details.templateId,
    details.mode ? details.mode.replaceAll("_", " ") : "",
    routingDetail,
    `${details.memberCount || 0} members`,
    (details.roles || []).map((role) => role.replaceAll("_", " ")).join(", "),
    details.roundPolicy === "synthesis_adaptive"
      ? "Synthesis-adaptive passes"
      : details.maxRounds
        ? `${details.maxRounds} round cap`
        : "",
    (details.profiles || [])
      .map((profile) => profile.displayName || profile.profileId || profile.channelRole)
      .filter(Boolean)
      .join(", "),
  ] : [];
  return [...workflowDetails, ...channelDetails].filter(Boolean).join(" | ");
}

function channelPlanOptionGuidance(option?: AgentSessionPlanOption): string {
  const mode = option?.submitDetails?.mode;
  if (mode === "multi_lens") {
    return "Independent multi-role review starts first; the synthesizer decides whether another round is needed before publishing a canonical PRD.";
  }
  if (mode === "clarification") {
    return "Guided mode starts with one facilitator. It does not automatically fan out or publish a canonical PRD.";
  }
  if (mode === "conversation") {
    return "One responder starts the conversation. Choose multi-lens when independent perspectives are required.";
  }
  return "";
}

function planQuestions(request: AgentSessionPlanRequest): AgentSessionPlanQuestion[] {
  return request.questions?.length ? request.questions : [{
    id: request.questionId,
    header: request.header,
    question: request.question,
    options: request.options,
    allowOther: request.allowOther,
  }];
}

function PlanSummary({
  request,
}: {
  request: AgentSessionPlanRequest;
}) {
  const questions = planQuestions(request);
  const answers = request.response?.answers?.length
    ? request.response.answers
    : request.response
      ? [{
        questionId: request.response.questionId,
        optionId: request.response.optionId,
        answer: request.response.answer,
      }]
      : [];
  const answerByQuestion = new Map(
    answers.map((answer) => [answer.questionId, answer]),
  );
  const selectedOption = answers.flatMap((answer) => (
    questions
      .find((question) => question.id === answer.questionId)
      ?.options.filter((option) => option.id === answer.optionId) || []
  )).find((option) => (
    option.submitMode === "apply" || option.submitMode === "propose"
  ));
  const detailText = planOptionDetails(selectedOption);
  const status = selectedOption?.submitMode === "propose"
    ? "Ready for confirmation"
    : selectedOption?.submitMode === "apply"
      ? "Plan applied"
      : "Plan complete";
  return (
    <div className="agent-plan-summary" title={request.question}>
      <div className="agent-plan-summary-title">
        <Check aria-hidden="true" size={15} />
        <strong>Plan summary</strong>
        <span>{status}</span>
      </div>
      <div className="agent-plan-summary-answers">
        {questions.map((question) => {
          const answer = answerByQuestion.get(question.id);
          if (!answer) return null;
          return (
            <div className="agent-plan-summary-answer" key={question.id}>
              <small>{question.question}</small>
              <strong>{answer.answer}</strong>
            </div>
          );
        })}
      </div>
      {detailText ? (
        <small className="agent-plan-option-details">{detailText}</small>
      ) : null}
    </div>
  );
}

export function PlanInteractionForm({
  busy = false,
  disabled = false,
  onChatAbout,
  onSubmit,
  request,
}: {
  busy?: boolean;
  disabled?: boolean;
  onChatAbout?: () => void;
  onSubmit?: (response: AgentSessionPlanResponse) => void;
  request: AgentSessionPlanRequest;
}) {
  const questions = planQuestions(request);
  if (request.response) {
    return <PlanSummary request={request} />;
  }
  return (
    <AskUserQuestion
      busy={busy}
      collapseOnDiscuss={false}
      disabled={disabled || !request.valid}
      discussLabel="Chat about"
      invalidMessage={request.validationError}
      onDiscuss={onChatAbout ? () => onChatAbout() : undefined}
      onSubmit={onSubmit
        ? (answers) => submitPlanAnswers(request, answers, onSubmit)
        : undefined}
      questions={questions.map((question) => ({
        id: question.id,
        header: question.header,
        question: question.question,
        allowOther: question.allowOther,
        required: true,
        options: question.options.map((option) => ({
          id: option.id,
          label: option.label,
          description: option.description,
          recommended: option.recommended,
          detail: planOptionDetails(option),
          guidance: channelPlanOptionGuidance(option),
          routeId: option.submitDetails?.routeId,
          submitAction: option.submitAction,
          submitMode: option.submitMode,
          templateId: option.submitDetails?.templateId,
        })),
      }))}
      requestId={`${request.requestEventId}:${request.revision}`}
      otherDescription=""
      otherLabel="Customize"
      submitLabel={(answers) => planSubmitLabel(request, questions, answers)}
    />
  );
}

function planSubmitLabel(
  request: AgentSessionPlanRequest,
  questions: AgentSessionPlanQuestion[],
  answers: AskUserQuestionAnswer[],
): string {
  const selectedOption = answers.flatMap((answer) => (
    questions
      .find((question) => question.id === answer.questionId)
      ?.options.filter((option) => option.id === answer.optionId) || []
  )).find((option) => (
    option.submitMode === "apply" || option.submitMode === "propose"
  ));
  const selectedMode = selectedOption?.submitMode || request.submitMode;
  return selectedMode === "apply" ? request.submitLabel || "Apply" : "Continue";
}

function submitPlanAnswers(
  request: AgentSessionPlanRequest,
  answers: AskUserQuestionAnswer[],
  onSubmit?: (response: AgentSessionPlanResponse) => void,
) {
  const primary = answers[0];
  if (!primary) return;
  onSubmit?.({
    requestEventId: request.requestEventId,
    requestId: request.requestId,
    revision: request.revision,
    questionId: primary.questionId,
    optionId: primary.optionId,
    answer: primary.answer,
    answers,
  });
}

export function ApproveInteractionActions({
  busy = false,
  onApprove,
  onReject,
  onRevise,
  proposal,
}: {
  busy?: boolean;
  onApprove?: () => void;
  onReject?: () => void;
  onRevise?: () => void;
  proposal: AgentSessionActionProposal;
}) {
  const presentation = actionPresentation(proposal.action);
  return (
    <div className="agent-approve-actions">
      <button
        className="agent-inline-button"
        data-testid="agent-proposal-revise"
        disabled={busy || !onRevise}
        type="button"
        onClick={onRevise}
      >
        <PencilLine aria-hidden="true" size={14} />
        Edit
      </button>
      <button
        className="agent-inline-button"
        data-testid="agent-proposal-reject"
        disabled={busy || !onReject}
        type="button"
        onClick={onReject}
      >
        <X aria-hidden="true" size={14} />
        Cancel
      </button>
      <button
        className="agent-inline-button primary agent-action-primary"
        data-testid="agent-proposal-approve"
        disabled={busy || !proposal.valid || !onApprove}
        type="button"
        onClick={onApprove}
      >
        <Check aria-hidden="true" size={14} />
        {busy ? presentation.busyLabel : presentation.confirmLabel}
      </button>
    </div>
  );
}
