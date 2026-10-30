import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Circle,
  HelpCircle,
  ShieldCheck,
  Square,
  SquareCheckBig,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { WebChatInteraction } from "../../api/types";
import {
  approvalDecisions,
  buildQuestionResponse,
  interactionTitle,
  isQuestionAnswered,
  normalizeWebChatQuestions,
  questionAnswer,
  type WebChatInteractionDraft,
} from "./webChatInteractionModel";

function decisionLabel(decision: string): string {
  const normalized = decision.replaceAll("_", " ").trim();
  return normalized ? normalized[0].toUpperCase() + normalized.slice(1) : "Continue";
}

function isApprovalAllowed(interaction: WebChatInteraction): boolean {
  return interaction.kind === "tool_approval";
}

function isApprovalDecision(decision: string): boolean {
  return !["deny", "denied", "reject", "rejected", "cancel"].includes(decision.toLowerCase());
}

export function WebChatInteractionPanel({
  busy = false,
  draft,
  error = "",
  interaction,
  mode,
  onCollapse,
  onDraftChange,
  onExpand,
  onRespond,
}: {
  busy?: boolean;
  draft: WebChatInteractionDraft;
  error?: string;
  interaction: WebChatInteraction;
  mode: "composer" | "thread";
  onCollapse?: () => void;
  onDraftChange: (draft: WebChatInteractionDraft) => void;
  onExpand?: () => void;
  onRespond: (response: Record<string, unknown>) => void;
}) {
  const questions = useMemo(() => normalizeWebChatQuestions(interaction), [interaction]);
  const [step, setStep] = useState(() => draft.questionIndex ?? 0);
  useEffect(() => {
    setStep(Math.min(draft.questionIndex ?? 0, Math.max(questions.length - 1, 0)));
  }, [draft.questionIndex, interaction.interaction_id, interaction.revision, questions.length]);
  const question = questions[Math.min(step, Math.max(questions.length - 1, 0))];
  const title = interactionTitle(interaction);
  const askUser = interaction.kind === "ask_user";

  const setQuestionDraft = (questionId: string, optionIds: string[], text: string) => {
    onDraftChange({
      answers: {
        ...draft.answers,
        [questionId]: { optionIds, text },
      },
      questionIndex: step,
    });
  };

  const setQuestionStep = (next: number) => {
    const bounded = Math.min(Math.max(next, 0), Math.max(questions.length - 1, 0));
    setStep(bounded);
    onDraftChange({ ...draft, questionIndex: bounded });
  };

  const allAnswered = questions.length > 0
    && questions.every((item) => isQuestionAnswered(draft, item));
  const locationLabel = mode === "composer" ? "Move question to conversation" : "Move question to composer";

  return (
    <section
      aria-label={title}
      className={`webchat-interaction-panel ${mode}`}
      data-interaction-kind={interaction.kind}
      data-plan-request-id={String(interaction.request.request_event_id || "") || undefined}
      data-testid={`webchat-interaction-${mode}`}
    >
      <header>
        <span className="webchat-interaction-icon" aria-hidden="true">
          {askUser ? <HelpCircle size={17} /> : <ShieldCheck size={17} />}
        </span>
        <span>
          <small>{askUser ? "Question" : "Confirmation"} · revision {interaction.revision}</small>
          <strong>{title}</strong>
        </span>
        {mode === "composer" && onCollapse ? (
          <button aria-label={locationLabel} title={locationLabel} type="button" onClick={onCollapse}>
            <ChevronDown size={16} />
          </button>
        ) : mode === "thread" && onExpand ? (
          <button aria-label={locationLabel} title={locationLabel} type="button" onClick={onExpand}>
            <ChevronUp size={16} />
          </button>
        ) : null}
      </header>

      {askUser && question ? (
        <div className="webchat-question-body">
          {questions.length > 1 ? (
            <div className="webchat-question-progress">
              <span>Question {step + 1} of {questions.length}</span>
              <span aria-label="Question progress">
                {questions.map((item, index) => (
                  <button
                    aria-current={index === step ? "step" : undefined}
                    aria-label={`Question ${index + 1}${isQuestionAnswered(draft, item) ? ", answered" : ""}`}
                    className={index === step ? "active" : ""}
                    key={item.id}
                    type="button"
                    onClick={() => setQuestionStep(index)}
                  >
                    <span />
                  </button>
                ))}
              </span>
            </div>
          ) : null}
          <div className="webchat-question-copy">
            {question.header && question.header !== title ? <small>{question.header}</small> : null}
            <p>{question.question}</p>
            {question.description ? <span>{question.description}</span> : null}
          </div>
          {question.options.length ? (
            <div
              aria-label={question.question}
              className="webchat-question-options"
              role={question.multiSelect ? "group" : "radiogroup"}
            >
              {question.options.map((option) => {
                const selected = questionAnswer(draft, question.id).optionIds.includes(option.id);
                const SelectIcon = question.multiSelect
                  ? selected ? SquareCheckBig : Square
                  : selected ? CheckCircle2 : Circle;
                return (
                  <button
                    aria-checked={selected}
                    className={selected ? "selected" : ""}
                    disabled={busy}
                    key={option.id}
                    role={question.multiSelect ? "checkbox" : "radio"}
                    type="button"
                    onClick={() => {
                      const current = questionAnswer(draft, question.id);
                      const optionIds = question.multiSelect
                        ? selected
                          ? current.optionIds.filter((id) => id !== option.id)
                          : [...current.optionIds, option.id]
                        : [option.id];
                      setQuestionDraft(question.id, optionIds, current.text);
                    }}
                  >
                    <SelectIcon size={17} />
                    <span>
                      <strong>{option.label}</strong>
                      {option.description ? <small>{option.description}</small> : null}
                    </span>
                  </button>
                );
              })}
              {question.allowOther ? (
                <button
                  aria-checked={questionAnswer(draft, question.id).optionIds.includes("other")}
                  className={questionAnswer(draft, question.id).optionIds.includes("other") ? "selected" : ""}
                  disabled={busy}
                  role={question.multiSelect ? "checkbox" : "radio"}
                  type="button"
                  onClick={() => {
                    const current = questionAnswer(draft, question.id);
                    const optionIds = question.multiSelect
                      ? current.optionIds.includes("other")
                        ? current.optionIds.filter((id) => id !== "other")
                        : [...current.optionIds, "other"]
                      : ["other"];
                    setQuestionDraft(question.id, optionIds, current.text);
                  }}
                >
                  {questionAnswer(draft, question.id).optionIds.includes("other") ? <CheckCircle2 size={17} /> : <Circle size={17} />}
                  <span><strong>Other</strong><small>Provide your own answer</small></span>
                </button>
              ) : null}
            </div>
          ) : null}
          {question.allowOther && (!question.options.length || questionAnswer(draft, question.id).optionIds.includes("other")) ? (
            <textarea
              autoFocus
              aria-label={`Answer for ${question.question}`}
              disabled={busy}
              placeholder="Type your answer"
              rows={2}
              value={questionAnswer(draft, question.id).text}
              onChange={(event) => {
                const current = questionAnswer(draft, question.id);
                setQuestionDraft(question.id, current.optionIds, event.target.value);
              }}
            />
          ) : null}
          <footer>
            <button className="secondary" disabled={busy} type="button" onClick={() => onRespond({ answer: "", skipped: true })}>
              Skip
            </button>
            <span />
            {questions.length > 1 ? (
              <>
                <button
                  aria-label="Previous question"
                  className="icon"
                  disabled={busy || step === 0}
                  type="button"
                  onClick={() => setQuestionStep(step - 1)}
                >
                  <ChevronLeft size={16} />
                </button>
                {step < questions.length - 1 ? (
                  <button
                    className="primary"
                    disabled={busy || !isQuestionAnswered(draft, question)}
                    type="button"
                    onClick={() => setQuestionStep(step + 1)}
                  >
                    Next <ChevronRight size={15} />
                  </button>
                ) : (
                  <button className="primary" disabled={busy || !allAnswered} type="button" onClick={() => onRespond(buildQuestionResponse(draft, questions))}>
                    <Check size={15} /> {busy ? "Submitting" : "Submit"}
                  </button>
                )}
              </>
            ) : (
              <button className="primary" disabled={busy || !allAnswered} type="button" onClick={() => onRespond(buildQuestionResponse(draft, questions))}>
                <Check size={15} /> {busy ? "Submitting" : "Submit"}
              </button>
            )}
          </footer>
        </div>
      ) : (
        <div className="webchat-approval-body">
          <p>{String(interaction.request.description || interaction.request.reason || `Review ${interaction.request.tool || interaction.request.action || "this request"} before continuing.`)}</p>
          {isApprovalAllowed(interaction) ? (
            <div className="webchat-approval-actions">
              {approvalDecisions(interaction).map((decision) => {
                const approved = isApprovalDecision(decision);
                return (
                  <button
                    className={approved ? "primary" : "secondary"}
                    disabled={busy}
                    key={decision}
                    type="button"
                    onClick={() => onRespond({ decision, approved })}
                  >
                    {approved ? <Check size={15} /> : <X size={15} />}
                    {decisionLabel(decision)}
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="webchat-interaction-route-note">
              Complete this request from its controlled Action or Artifact surface.
            </p>
          )}
          <details>
            <summary>Request details</summary>
            <pre>{JSON.stringify(interaction.request, null, 2)}</pre>
          </details>
        </div>
      )}
      {error ? <div className="webchat-interaction-error" role="alert">{error}</div> : null}
    </section>
  );
}
