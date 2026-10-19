import type { WebChatInteraction } from "../../api/types";

export interface WebChatQuestionOption {
  id: string;
  label: string;
  description: string;
  recommended: boolean;
}

export interface WebChatQuestion {
  id: string;
  header: string;
  question: string;
  description: string;
  options: WebChatQuestionOption[];
  allowOther: boolean;
  multiSelect: boolean;
}

export interface WebChatQuestionAnswerDraft {
  optionIds: string[];
  text: string;
}

export interface WebChatInteractionDraft {
  answers: Record<string, WebChatQuestionAnswerDraft>;
  questionIndex?: number;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function bool(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function normalizeOptions(value: unknown): WebChatQuestionOption[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((raw, index) => {
    if (typeof raw === "string") {
      const label = raw.trim();
      return label ? [{ id: label, label, description: "", recommended: false }] : [];
    }
    if (!raw || typeof raw !== "object") return [];
    const option = raw as Record<string, unknown>;
    const label = text(option.label) || text(option.value) || text(option.id);
    if (!label) return [];
    return [{
      id: text(option.id) || text(option.value) || `option-${index + 1}`,
      label,
      description: text(option.description),
      recommended: option.recommended === true || /\(recommended\)/i.test(label),
    }];
  });
}

function normalizeQuestion(
  raw: Record<string, unknown>,
  index: number,
  parent: Record<string, unknown>,
): WebChatQuestion {
  const options = normalizeOptions(raw.options ?? raw.choices ?? parent.options ?? parent.choices);
  const explicitOther = bool(raw.allowOther ?? raw.allow_other ?? raw.isOther ?? raw.is_other
    ?? parent.allowOther ?? parent.allow_other ?? parent.isOther);
  return {
    id: text(raw.id) || text(raw.question_id) || `question-${index + 1}`,
    header: text(raw.header) || text(parent.header) || `Question ${index + 1}`,
    question: text(raw.question) || text(raw.text) || text(parent.question) || text(parent.prompt),
    description: text(raw.description) || text(parent.description),
    options,
    allowOther: explicitOther ?? options.length === 0,
    multiSelect: raw.multiSelect === true || raw.multi_select === true,
  };
}

export function interactionKey(interaction: WebChatInteraction): string {
  return `${interaction.interaction_id}:${interaction.revision}`;
}

export function interactionTitle(interaction: WebChatInteraction): string {
  const request = interaction.request ?? {};
  return text(request.header)
    || text(request.title)
    || (interaction.kind === "ask_user" ? "Input required" : "Approval required");
}

export function normalizeWebChatQuestions(interaction: WebChatInteraction): WebChatQuestion[] {
  const request = interaction.request ?? {};
  const rawQuestions = Array.isArray(request.questions)
    ? request.questions.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const source = rawQuestions.length ? rawQuestions : [request];
  return source.map((question, index) => normalizeQuestion(question, index, request));
}

export function emptyInteractionDraft(): WebChatInteractionDraft {
  return { answers: {} };
}

export function interactionDraftStorageKey(
  projectId: string,
  conversationId: string,
  threadId: string,
): string {
  return `zf.webchat.interaction-drafts:${projectId}:${conversationId}:${threadId}`;
}

export function loadInteractionDrafts(
  storage: Pick<Storage, "getItem"> | null,
  key: string,
): Record<string, WebChatInteractionDraft> {
  if (!storage) return {};
  try {
    const value = JSON.parse(storage.getItem(key) || "{}") as unknown;
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).flatMap(([interaction, raw]) => {
        if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
        const answers = (raw as Record<string, unknown>).answers;
        if (!answers || typeof answers !== "object" || Array.isArray(answers)) return [];
        const normalizedAnswers = Object.fromEntries(
          Object.entries(answers).flatMap(([questionId, answer]) => {
            if (!answer || typeof answer !== "object" || Array.isArray(answer)) return [];
            const item = answer as Record<string, unknown>;
            const optionIds = Array.isArray(item.optionIds)
              ? item.optionIds.filter((option): option is string => typeof option === "string")
              : [];
            return [[questionId, {
              optionIds,
              text: typeof item.text === "string" ? item.text : "",
            }]];
          }),
        );
        const questionIndex = Number((raw as Record<string, unknown>).questionIndex);
        return [[interaction, {
          answers: normalizedAnswers,
          ...(Number.isSafeInteger(questionIndex) && questionIndex >= 0
            ? { questionIndex }
            : {}),
        }]];
      }),
    );
  } catch {
    return {};
  }
}

export function saveInteractionDrafts(
  storage: Pick<Storage, "setItem"> | null,
  key: string,
  drafts: Record<string, WebChatInteractionDraft>,
): void {
  storage?.setItem(key, JSON.stringify(drafts));
}

export function questionAnswer(
  draft: WebChatInteractionDraft,
  questionId: string,
): WebChatQuestionAnswerDraft {
  return draft.answers[questionId] ?? { optionIds: [], text: "" };
}

export function isQuestionAnswered(
  draft: WebChatInteractionDraft,
  question: WebChatQuestion,
): boolean {
  const answer = questionAnswer(draft, question.id);
  if (!question.options.length) return Boolean(answer.text.trim());
  if (answer.optionIds.includes("other")) {
    return question.allowOther && Boolean(answer.text.trim());
  }
  return answer.optionIds.some((id) => question.options.some((option) => option.id === id));
}

export function buildQuestionResponse(
  draft: WebChatInteractionDraft,
  questions: WebChatQuestion[],
): Record<string, unknown> {
  const answers = questions.map((question) => {
    const value = questionAnswer(draft, question.id);
    const other = question.allowOther && value.optionIds.includes("other");
    const optionIds = other && !question.multiSelect ? ["other"] : value.optionIds;
    const labels = optionIds.flatMap((id) => {
      const option = question.options.find((option) => option.id === id);
      return option ? [option.label] : [];
    });
    const parts = [...labels, other || !question.options.length ? value.text.trim() : ""].filter(Boolean);
    return {
      question_id: question.id,
      option_id: optionIds[0] ?? "",
      option_ids: optionIds,
      answer: parts.join(", "),
    };
  });
  return {
    answer: answers.map((item) => item.answer).filter(Boolean).join("\n"),
    answers,
    ...(answers.length === 1 ? {
      question_id: answers[0].question_id,
      option_id: answers[0].option_id,
    } : {}),
  };
}

export function approvalDecisions(interaction: WebChatInteraction): string[] {
  const request = interaction.request ?? {};
  const configured = request.allowed_decisions ?? request.choices;
  if (!Array.isArray(configured)) return ["approve", "deny"];
  const decisions = configured.map((item) => {
    if (!item || typeof item !== "object") return text(item);
    const decision = item as Record<string, unknown>;
    return text(decision.id) || text(decision.value) || text(decision.label);
  }).filter(Boolean);
  return decisions.length ? [...new Set(decisions)] : ["approve", "deny"];
}
