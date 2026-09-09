import {
  ArrowLeft,
  Check,
  ChevronRight,
  MessageCircle,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

export interface AskUserQuestionOption {
  id: string;
  label: string;
  description?: string;
  recommended?: boolean;
  detail?: string;
  guidance?: string;
  routeId?: string;
  submitAction?: string;
  submitMode?: string;
  templateId?: string;
}

export interface AskUserQuestionItem {
  id: string;
  header: string;
  question: string;
  description?: string;
  options?: AskUserQuestionOption[];
  allowOther?: boolean;
  inputPlaceholder?: string;
  initialAnswer?: string;
  required?: boolean;
  skipLabel?: string;
}

export interface AskUserQuestionAnswer {
  questionId: string;
  optionId: string;
  answer: string;
  skipped?: boolean;
}

interface AskUserQuestionProps {
  busy?: boolean;
  className?: string;
  collapseOnDiscuss?: boolean;
  disabled?: boolean;
  discussLabel?: string;
  invalidMessage?: string;
  onClose?: () => void;
  onDiscuss?: (question: AskUserQuestionItem) => void;
  onSubmit?: (answers: AskUserQuestionAnswer[]) => void;
  otherDescription?: string;
  otherLabel?: string;
  questions: AskUserQuestionItem[];
  requestId: string;
  submitLabel?: string | ((answers: AskUserQuestionAnswer[]) => string);
  variant?: "embedded" | "shelf";
}

export function AskUserQuestion({
  busy = false,
  className = "",
  collapseOnDiscuss = true,
  disabled = false,
  discussLabel = "Discuss",
  invalidMessage = "",
  onClose,
  onDiscuss,
  onSubmit,
  otherDescription = "Provide another constraint or decision.",
  otherLabel = "Other",
  questions,
  requestId,
  submitLabel = "Continue",
  variant = "embedded",
}: AskUserQuestionProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [collapsed, setCollapsed] = useState(false);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [skipped, setSkipped] = useState<Record<string, boolean>>({});
  const [textAnswers, setTextAnswers] = useState<Record<string, string>>({});

  useEffect(() => {
    setActiveIndex(0);
    setCollapsed(false);
    setSelected({});
    setSkipped({});
    setTextAnswers(Object.fromEntries(
      questions
        .filter((question) => question.initialAnswer)
        .map((question) => [question.id, question.initialAnswer || ""]),
    ));
  }, [requestId]);

  const safeQuestions = questions.slice(0, 3);
  const question = safeQuestions[activeIndex] || safeQuestions[0];
  const answers = useMemo(
    () => safeQuestions.flatMap((item): AskUserQuestionAnswer[] => {
      if (skipped[item.id]) {
        return [{ questionId: item.id, optionId: "skip", answer: "", skipped: true }];
      }
      const options = item.options || [];
      if (!options.length) {
        const answer = (textAnswers[item.id] || "").trim();
        return answer ? [{ questionId: item.id, optionId: "text", answer }] : [];
      }
      const optionId = selected[item.id] || "";
      if (!optionId) return [];
      if (optionId === "other") {
        const answer = (textAnswers[item.id] || "").trim();
        return answer ? [{ questionId: item.id, optionId, answer }] : [];
      }
      const option = options.find((candidate) => candidate.id === optionId);
      return option
        ? [{ questionId: item.id, optionId, answer: option.label }]
        : [];
    }),
    [safeQuestions, selected, skipped, textAnswers],
  );

  if (!question) return null;

  const currentAnswer = answers.find((item) => item.questionId === question.id);
  const currentSelected = selected[question.id] || "";
  const isLast = activeIndex === safeQuestions.length - 1;
  const canContinue = Boolean(
    currentAnswer
    && !busy
    && !disabled
    && !invalidMessage
    && Boolean(onSubmit),
  );
  const resolvedSubmitLabel = typeof submitLabel === "function"
    ? submitLabel(answers)
    : submitLabel;

  function continueQuestion() {
    if (!canContinue) return;
    if (!isLast) {
      setActiveIndex((index) => Math.min(index + 1, safeQuestions.length - 1));
      return;
    }
    if (answers.length === safeQuestions.length) onSubmit?.(answers);
  }

  function skipQuestion() {
    if (question.required !== false || busy || disabled) return;
    const skipped: AskUserQuestionAnswer = {
      questionId: question.id,
      optionId: "skip",
      answer: "",
      skipped: true,
    };
    if (!isLast) {
      setSkipped((current) => ({ ...current, [question.id]: true }));
      setActiveIndex((index) => Math.min(index + 1, safeQuestions.length - 1));
      return;
    }
    onSubmit?.([
      ...answers.filter((answer) => answer.questionId !== question.id),
      skipped,
    ]);
  }

  if (collapsed) {
    return (
      <button
        className={`ask-user-collapsed ${variant} ${className}`.trim()}
        data-question-id={question.id}
        data-request-id={requestId}
        data-testid="ask-user-question-collapsed"
        type="button"
        onClick={() => setCollapsed(false)}
      >
        <span className="ask-user-state">Input needed</span>
        <span className="ask-user-collapsed-copy">
          <strong>{safeQuestions.length - activeIndex} question{safeQuestions.length - activeIndex === 1 ? "" : "s"} waiting</strong>
          <small>{question.question}</small>
        </span>
        <ChevronRight aria-hidden="true" size={17} />
      </button>
    );
  }

  return (
    <section
      aria-busy={busy}
      aria-label="Ask user question"
      className={`ask-user-question ${variant} agent-plan-form ${className}`.trim()}
      data-question-id={question.id}
      data-request-id={requestId}
      data-testid="ask-user-question"
    >
      <div className="ask-user-head">
        <div>
          <div className="ask-user-kicker">
            <span>{question.header || "Question"}</span>
            <span>{activeIndex + 1} of {safeQuestions.length}</span>
          </div>
          <h3>{question.question}</h3>
          {question.description ? <p>{question.description}</p> : null}
          {safeQuestions.length > 1 ? (
            <div aria-label="Question progress" className="ask-user-progress">
              {safeQuestions.map((item, index) => (
                <i
                  className={index < activeIndex ? "done" : index === activeIndex ? "active" : ""}
                  key={item.id}
                />
              ))}
            </div>
          ) : null}
        </div>
        <button
          aria-label="Close question"
          className="icon-button ask-user-close"
          title={onClose ? "Close question" : "Collapse question"}
          type="button"
          onClick={() => {
            if (onClose) {
              onClose();
              return;
            }
            setCollapsed(true);
          }}
        >
          <X aria-hidden="true" size={15} />
        </button>
      </div>

      <fieldset
        className="ask-user-fieldset agent-plan-question"
        disabled={busy || disabled || Boolean(invalidMessage)}
      >
        <legend className="sr-only">{question.question}</legend>
        {(question.options || []).length ? (
          <div className="ask-user-options agent-plan-options">
            {(question.options || []).map((option) => (
              <label
                className={`ask-user-option agent-plan-option ${currentSelected === option.id ? "selected" : ""}`}
                key={option.id}
              >
                <span className="ask-user-option-index">
                  <input
                    aria-label={option.label}
                    checked={currentSelected === option.id}
                    data-option-id={option.id}
                    data-recommended={option.recommended ? "true" : undefined}
                    data-route-id={option.routeId || undefined}
                    data-submit-action={option.submitAction || undefined}
                    data-submit-mode={option.submitMode || undefined}
                    data-template-id={option.templateId || undefined}
                    data-testid={`ask-user-option-${option.id}`}
                    name={`ask-${requestId}-${question.id}`}
                    type="radio"
                    value={option.id}
                    onChange={() => setSelected((current) => ({
                      ...current,
                      [question.id]: option.id,
                    }))}
                    onClick={() => setSkipped((current) => ({ ...current, [question.id]: false }))}
                  />
                </span>
                <span className="ask-user-option-copy">
                  <span className="ask-user-option-heading agent-plan-option-heading">
                    <strong>{displayOptionLabel(option.label)}</strong>
                    {option.recommended ? (
                      <small className="ask-user-recommended agent-plan-recommended">Recommended</small>
                    ) : null}
                  </span>
                  {option.description ? <small>{option.description}</small> : null}
                  {option.detail ? <small className="agent-plan-option-details">{option.detail}</small> : null}
                  {option.guidance ? <small className="agent-plan-option-guidance">{option.guidance}</small> : null}
                </span>
              </label>
            ))}
            {question.allowOther !== false ? (
              <label
                className={`ask-user-option agent-plan-option other ${currentSelected === "other" ? "selected" : ""}`}
              >
                <span className="ask-user-option-index">
                  <input
                    aria-label={otherLabel}
                    checked={currentSelected === "other"}
                    data-testid="ask-user-option-other"
                    name={`ask-${requestId}-${question.id}`}
                    type="radio"
                    value="other"
                    onChange={() => setSelected((current) => ({
                      ...current,
                      [question.id]: "other",
                    }))}
                    onClick={() => setSkipped((current) => ({ ...current, [question.id]: false }))}
                  />
                </span>
                <span className="ask-user-option-copy">
                  <strong>{otherLabel}</strong>
                  {otherDescription ? <small>{otherDescription}</small> : null}
                </span>
              </label>
            ) : null}
            {currentSelected === "other" ? (
              <textarea
                aria-label="Custom answer"
                className="ask-user-textarea"
                placeholder={question.inputPlaceholder || "Describe the answer you want to use."}
                value={textAnswers[question.id] || ""}
                onChange={(event) => setTextAnswers((current) => ({
                  ...current,
                  [question.id]: event.target.value,
                }))}
                onInput={() => setSkipped((current) => ({ ...current, [question.id]: false }))}
              />
            ) : null}
          </div>
        ) : (
          <textarea
            aria-label={question.question}
            className="ask-user-textarea direct"
            placeholder={question.inputPlaceholder || "Type your answer."}
            value={textAnswers[question.id] || ""}
            onChange={(event) => setTextAnswers((current) => ({
              ...current,
              [question.id]: event.target.value,
            }))}
            onInput={() => setSkipped((current) => ({ ...current, [question.id]: false }))}
          />
        )}
      </fieldset>

      {invalidMessage ? (
        <p className="agent-plan-invalid" role="alert">{invalidMessage}</p>
      ) : null}

      <div className="ask-user-actions agent-plan-actions">
        {activeIndex > 0 ? (
          <button
            className="agent-inline-button quiet"
            disabled={busy}
            type="button"
            onClick={() => setActiveIndex((index) => Math.max(index - 1, 0))}
          >
            <ArrowLeft aria-hidden="true" size={14} />
            Back
          </button>
        ) : null}
        {onDiscuss ? (
          <button
            className="agent-inline-button quiet"
            disabled={busy}
            type="button"
            onClick={() => {
              if (collapseOnDiscuss) setCollapsed(true);
              onDiscuss(question);
            }}
          >
            <MessageCircle aria-hidden="true" size={14} />
            {discussLabel}
          </button>
        ) : null}
        {question.required === false ? (
          <button
            className="agent-inline-button quiet"
            disabled={busy || disabled}
            type="button"
            onClick={skipQuestion}
          >
            {question.skipLabel || "Skip"}
          </button>
        ) : (
          <span className="ask-user-required">Required to continue</span>
        )}
        <button
          className="agent-inline-button primary ask-user-submit"
          data-testid="ask-user-submit"
          disabled={!canContinue}
          type="button"
          onClick={continueQuestion}
        >
          {isLast ? <Check aria-hidden="true" size={14} /> : null}
          {isLast ? resolvedSubmitLabel : "Next question"}
          {!isLast ? <ChevronRight aria-hidden="true" size={14} /> : null}
        </button>
      </div>
    </section>
  );
}

export function displayOptionLabel(label: string): string {
  return label
    .replace(/\s*\((?:Recommended|推荐)\)\s*$/i, "")
    .trim();
}
