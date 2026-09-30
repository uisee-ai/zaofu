import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import { useEffect, useMemo, useRef, useState } from "react";
import { selfIssueIntakeSubmissionBlocker } from "./selfIssue";

type UnknownRecord = Record<string, unknown>;

export interface SelfIssueIntakeWizardProps {
  intake: UnknownRecord;
  busy: boolean;
  onAddAttachment: (file: File, videoDisclosureConfirmed: boolean) => Promise<void>;
  onCancel: () => Promise<void>;
  onMinimize: () => void;
  onRemoveAttachment: (attachmentId: string) => Promise<void>;
  onSave: (answers: UnknownRecord, currentStep: number) => Promise<void>;
  onSubmit: (
    answers: UnknownRecord,
    attachmentDisclosureConfirmed: boolean,
  ) => Promise<{ missingQuestionId?: string; attachmentDisclosureRequired?: boolean } | void>;
}

export function SelfIssueIntakeWizard({
  intake,
  busy,
  onAddAttachment,
  onCancel,
  onMinimize,
  onRemoveAttachment,
  onSave,
  onSubmit,
}: SelfIssueIntakeWizardProps) {
  useSettingsPreferences();
  const questions = useMemo(() => recordArray(intake.questions), [intake.questions]);
  const attachments = useMemo(() => recordArray(intake.attachments), [intake.attachments]);
  const intakeId = text(intake.intake_id);
  const storageKey = `zf.selfIssueIntake:${intakeId}`;
  const initialAnswers = useMemo(() => {
    const canonical = record(intake.answers) ?? {};
    if (typeof window === "undefined" || !intakeId) return canonical;
    try {
      const local = JSON.parse(window.localStorage.getItem(storageKey) ?? "null");
      return local && typeof local === "object" && !Array.isArray(local)
        ? { ...canonical, ...(local as UnknownRecord) }
        : canonical;
    } catch {
      return canonical;
    }
  }, [intake.answers, intakeId, storageKey]);
  const [answers, setAnswers] = useState<UnknownRecord>(initialAnswers);
  const [step, setStep] = useState(() => Math.max(0, Math.min(
    Number(intake.current_step || 0), Math.max(questions.length - 1, 0),
  )));
  const [errorQuestion, setErrorQuestion] = useState("");
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [videoConfirmed, setVideoConfirmed] = useState(false);
  const [attachmentDisclosureError, setAttachmentDisclosureError] = useState(false);
  const queuedRef = useRef<{ answers: UnknownRecord; step: number } | null>(null);
  const timerRef = useRef<number | null>(null);
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const pendingSaveCountRef = useRef(0);

  useEffect(() => {
    setAnswers(initialAnswers);
  }, [initialAnswers, intakeId]);

  useEffect(() => {
    if (attachments.length === 0) setAttachmentDisclosureError(false);
  }, [attachments.length]);

  useEffect(() => () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
  }, []);

  const current = questions[step] ?? {};
  const questionId = text(current.id);
  const required = current.required === true;
  const inputKind = text(current.input_kind) || "text";
  const options = recordArray(current.options);
  const currentTextAnswer = text(answers[questionId]);

  function updateAnswer(value: unknown) {
    if (submitted) return;
    const next = { ...answers, [questionId]: value };
    setAnswers(next);
    setErrorQuestion((existing) => existing === questionId && answerPresent(value) ? "" : existing);
    if (intakeId) window.localStorage.setItem(storageKey, JSON.stringify(next));
    queueSave(next, step);
  }

  function queueSave(nextAnswers: UnknownRecord, nextStep: number) {
    queuedRef.current = { answers: nextAnswers, step: nextStep };
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => void flushSave(), 550);
  }

  async function flushSave() {
    if (!queuedRef.current) return;
    const pending = queuedRef.current;
    queuedRef.current = null;
    timerRef.current = null;
    await persist(pending.answers, pending.step);
  }

  function persist(nextAnswers: UnknownRecord, nextStep: number): Promise<void> {
    pendingSaveCountRef.current += 1;
    setSaving(true);
    const operation = saveChainRef.current
      .catch(() => undefined)
      .then(() => onSave(nextAnswers, nextStep));
    saveChainRef.current = operation.then(() => undefined, () => undefined);
    return operation.finally(() => {
      pendingSaveCountRef.current -= 1;
      if (pendingSaveCountRef.current === 0) setSaving(false);
    });
  }

  async function move(nextStep: number) {
    if (submitted) return;
    const bounded = Math.max(0, Math.min(nextStep, questions.length - 1));
    setStep(bounded);
    queuedRef.current = null;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    await persist(answers, bounded);
  }

  async function submit() {
    if (submitting || submitted) return;
    const blocker = selfIssueIntakeSubmissionBlocker(
      questions, answers, attachments.length, videoConfirmed,
    );
    if (blocker?.reason === "required") {
      const missingIndex = questions.findIndex((item) => text(item.id) === blocker.questionId);
      if (missingIndex >= 0) setStep(missingIndex);
      setErrorQuestion(blocker.questionId);
      return;
    }
    if (blocker?.reason === "attachment_disclosure") {
      const attachmentIndex = questions.findIndex((item) => text(item.id) === "attachments_context");
      if (attachmentIndex >= 0) setStep(attachmentIndex);
      setAttachmentDisclosureError(true);
      return;
    }
    setSubmitting(true);
    setSubmitted(true);
    queuedRef.current = null;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    try {
      await saveChainRef.current;
      const result = await onSubmit(answers, videoConfirmed);
      const missing = result?.missingQuestionId ?? "";
      if (result?.attachmentDisclosureRequired) {
        const attachmentIndex = questions.findIndex((item) => text(item.id) === "attachments_context");
        if (attachmentIndex >= 0) setStep(attachmentIndex);
        setAttachmentDisclosureError(true);
        setSubmitted(false);
        return;
      }
      if (!missing) {
        window.localStorage.removeItem(storageKey);
        return;
      }
      const missingIndex = questions.findIndex((item) => text(item.id) === missing);
      if (missingIndex >= 0) setStep(missingIndex);
      setErrorQuestion(missing);
      setSubmitted(false);
    } catch (error) {
      setSubmitted(false);
      throw error;
    } finally {
      setSubmitting(false);
    }
  }

  async function cancel() {
    if (submitting || submitted) return;
    queuedRef.current = null;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    await saveChainRef.current;
    await onCancel();
  }

  const environment = record(answers.environment) ?? {};
  return (
    <section className="self-issue-intake" aria-label="Self-Issue questions">
      <header className="self-issue-intake-header">
        <div>
          <strong>Report a ZaoFu bug</strong>
          <small>
            {text(intake.origin) === "system_detected"
              ? "ZaoFu detected a strong internal incident signal. Review and complete this local report; nothing is published automatically."
              : "Answers are saved locally until you submit all eight questions."}
          </small>
        </div>
        <div className="self-issue-intake-progress">
          <span aria-label={`Question ${step + 1} of ${questions.length}`}>{step + 1}/{questions.length}</span>
          <button
            aria-label="Minimize Self-Issue questions"
            className="self-issue-intake-minimize"
            title="Minimize"
            type="button"
            onClick={onMinimize}
          >−</button>
        </div>
      </header>

      <div className="self-issue-intake-question" key={questionId}>
        <label htmlFor={`self-issue-${questionId}`}>
          {text(current.title)}
          {required ? <span className="self-issue-required" aria-label="required">*</span> : null}
        </label>
        {text(current.help_text) ? <small>{text(current.help_text)}</small> : null}
        {options.length ? (
          <div className="self-issue-intake-options" aria-label="Quick answers">
            {options.map((option) => {
              const value = text(option.value);
              const selected = currentTextAnswer === value;
              return (
                <button
                  aria-pressed={selected}
                  className={`${selected ? "selected" : ""} ${currentTextAnswer && !selected ? "muted" : ""}`}
                  key={value}
                  type="button"
                  onClick={() => updateAnswer(selected ? "" : value)}
                >
                  {text(option.label) || value}
                </button>
              );
            })}
          </div>
        ) : null}
        {inputKind === "textarea" || inputKind === "attachments" ? (
          <textarea
            id={`self-issue-${questionId}`}
            className={errorQuestion === questionId ? "invalid" : ""}
            placeholder={text(current.placeholder)}
            value={currentTextAnswer}
            onChange={(event) => updateAnswer(event.target.value)}
          />
        ) : inputKind === "environment" ? (
          <div className="self-issue-environment-fields">
            <select
              aria-label="Operating system"
              value={text(environment.os)}
              onChange={(event) => updateAnswer({ ...environment, os: event.target.value })}
            >
              <option value="">Select an OS</option>
              {['Linux', 'macOS', 'Windows', 'Other'].map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
            <input
              id={`self-issue-${questionId}`}
              placeholder="Operating system version"
              value={text(environment.version)}
              onChange={(event) => updateAnswer({ ...environment, version: event.target.value })}
            />
          </div>
        ) : (
          <input
            id={`self-issue-${questionId}`}
            className={errorQuestion === questionId ? "invalid" : ""}
            placeholder={text(current.placeholder)}
            value={currentTextAnswer}
            onChange={(event) => updateAnswer(event.target.value)}
          />
        )}
        {errorQuestion === questionId ? (
          <div className="self-issue-intake-error" role="alert">This question can not be empty</div>
        ) : null}
        {inputKind === "attachments" ? (
          <div className="self-issue-attachment-picker">
            <label className="self-issue-video-confirmation">
              <input
                checked={videoConfirmed}
                type="checkbox"
                onChange={(event) => {
                  setVideoConfirmed(event.target.checked);
                  if (event.target.checked) setAttachmentDisclosureError(false);
                }}
              />
              I understand that attached files will follow the GitLab project visibility.
            </label>
            {attachmentDisclosureError ? (
              <div className="self-issue-intake-error" role="alert">
                Confirm attachment visibility before submitting this report.
              </div>
            ) : null}
            <input
              aria-label="Add screenshots, videos, logs, documents, or data files"
              accept=".png,.jpg,.jpeg,.mp4,.webm,.txt,.log,.json,.md,.xml,.yaml,.yml,.csv,.pdf,.doc,.docx,.xls,.xlsx"
              disabled={busy || uploading || attachments.length >= 5}
              multiple
              type="file"
              onChange={(event) => void (async () => {
                const files = Array.from(event.target.files ?? []);
                setUploading(true);
                try {
                  for (const file of files) {
                    if (file.type.startsWith("video/") && !videoConfirmed) {
                      setErrorQuestion(questionId);
                      return;
                    }
                    await onAddAttachment(file, videoConfirmed);
                  }
                } finally {
                  setUploading(false);
                  event.target.value = "";
                }
              })()}
            />
            <ul>
              {attachments.map((item) => (
                <li key={text(item.attachment_id)}>
                  <span>{text(item.filename)} · {formatBytes(Number(item.byte_count || 0))}</span>
                  <button
                    disabled={busy || uploading}
                    type="button"
                    onClick={() => void onRemoveAttachment(text(item.attachment_id))}
                  >{uiText("Remove")}</button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <footer className="self-issue-intake-actions">
        <button disabled={busy || uploading || submitting || submitted || step === 0} type="button" onClick={() => void move(step - 1)}>{uiText("Back")}</button>
        <button className="quiet" disabled={busy || uploading || submitting || submitted} type="button" onClick={() => void cancel()}>{uiText("Cancel")}</button>
        <span>{submitted ? "Submitted" : saving ? "Saving…" : "Saved locally"}</span>
        {step < questions.length - 1 ? (
          <button disabled={busy || uploading || submitting || submitted} type="button" onClick={() => void move(step + 1)}>{uiText("Next")}</button>
        ) : (
          <button disabled={busy || uploading || submitting || submitted} type="button" onClick={() => void submit()}>{submitted ? "Submitted" : "Submit answers"}</button>
        )}
      </footer>
    </section>
  );
}

function answerPresent(value: unknown): boolean {
  if (typeof value === "string") return Boolean(value.trim());
  return Boolean(value && typeof value === "object");
}

function record(value: unknown): UnknownRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as UnknownRecord
    : null;
}

function recordArray(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.flatMap((item) => record(item) ? [record(item)!] : []) : [];
}

function text(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
