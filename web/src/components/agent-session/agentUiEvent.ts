import type { EventRecord } from "../../api/types";
import type {
  AgentPartKind,
  AgentSessionActionProposal,
  AgentSessionPlanOption,
  AgentSessionPlanRequest,
  AgentSessionPlanResponse,
  AgentSessionStatus,
} from "./types";

export const AGENT_UI_EVENT_SCHEMA_VERSION = "agent-ui-event.v1";

export const AGENT_STREAM_DELTA_EVENT_TYPES = new Set([
  "agent.session.part.delta",
  "kanban.agent.turn.delta",
  "kanban.agent.message.delta",
  "channel.message.stream.delta",
]);

export const AGENT_STREAM_TERMINAL_EVENT_TYPES = new Set([
  "agent.session.run.completed",
  "agent.session.run.failed",
  "agent.session.run.cancelled",
  "agent.session.part.completed",
  "agent.session.part.failed",
  "kanban.agent.reply",
  "kanban.agent.turn.completed",
  "kanban.agent.turn.failed",
  "channel.message.stream.ended",
]);

export function isAgentStreamDeltaEvent(type: string): boolean {
  return AGENT_STREAM_DELTA_EVENT_TYPES.has(type);
}

export function isAgentStreamTerminalEvent(type: string): boolean {
  return AGENT_STREAM_TERMINAL_EVENT_TYPES.has(type);
}

export function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

export function recordValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function recordString(row: Record<string, unknown> | null | undefined, key: string, fallback = ""): string {
  const value = row?.[key];
  if (value === null || value === undefined) return fallback;
  return String(value);
}

export function stringify(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export function agentRunStatus(value: string): AgentSessionStatus {
  if (value === "running" || value === "started" || value === "streaming" || value === "typing") return "streaming";
  if (value === "submitted") return "submitted";
  if (value === "queued" || value === "pending") return "queued";
  if (value === "waiting_input") return "waiting_input";
  if (value === "failed" || value === "rejected" || value === "escalated") return "failed";
  if (value === "cancelled" || value === "canceled") return "cancelled";
  if (value === "stale") return "stale";
  if (value === "completed" || value === "done") return "completed";
  return "idle";
}

export function agentPartKindFromValue(value: string): AgentPartKind {
  if (value === "thinking" || value === "reasoning") return "thinking";
  if (value === "tool_call" || value === "tool_use") return "tool_call";
  if (value === "tool_result" || value === "result") return "tool_result";
  if (value === "tool") return "tool";
  if (value === "command" || value === "command_output") return "command";
  if (value === "file_read") return "file_read";
  if (value === "file_change" || value === "file_changed") return "file_change";
  if (value === "code_preview" || value === "code") return "code_preview";
  if (value === "diff_preview" || value === "diff" || value === "patch") return "diff_preview";
  if (value === "test_result" || value === "test") return "test_result";
  if (value === "artifact_preview" || value === "artifact") return "artifact_preview";
  if (value === "trace_ref" || value === "trace") return "trace_ref";
  if (value === "action_proposal") return "action_proposal";
  if (value === "approval_request" || value === "approval") return "approval_request";
  if (value === "context_ledger" || value === "context") return "context_ledger";
  if (value === "question" || value === "input_request" || value === "user_input_request") return "question";
  if (value === "proposal") return "proposal";
  if (value === "error") return "error";
  if (value === "text") return "text";
  return "status";
}

export function agentPartTitle(value: string): string {
  const kind = agentPartKindFromValue(value);
  if (kind === "thinking") return "Thinking";
  if (kind === "text") return "Response";
  if (kind === "tool" || kind === "tool_call" || kind === "tool_result") return "Tool";
  if (kind === "command") return "Command";
  if (kind === "test_result") return "Test result";
  if (kind === "file_read") return "File read";
  if (kind === "file_change") return "File change";
  if (kind === "code_preview") return "Code preview";
  if (kind === "diff_preview") return "Diff preview";
  if (kind === "artifact_preview") return "Artifact preview";
  if (kind === "trace_ref") return "Trace";
  if (kind === "action_proposal") return "Action proposal";
  if (kind === "context_ledger") return "Context";
  if (kind === "question") return "Question";
  if (kind === "proposal") return "Proposal";
  if (kind === "error") return "Error";
  return "Status";
}

export function agentToolTitle(tool: string): string {
  const normalized = tool.toLowerCase();
  if (["bash", "exec", "exec_command"].includes(normalized)) return "Run command";
  if (["read", "glob"].includes(normalized)) return "Read file";
  if (["grep", "rg", "search"].includes(normalized)) return "Search";
  if (["write", "edit", "multi_edit", "patch_apply"].includes(normalized)) return "Edit";
  return tool ? `Tool ${tool}` : "Tool";
}

export function agentDeltaKind(payload: Record<string, unknown>): AgentPartKind {
  const type = textValue(payload.message_type || payload.type || payload.kind).trim();
  return agentPartKindFromValue(type || "status");
}

export function agentDeltaContent(payload: Record<string, unknown>): string {
  const type = textValue(payload.message_type || payload.type || payload.kind).trim();
  if (type === "text" || type === "thinking" || type === "reasoning") {
    return textValue(payload.content ?? payload.delta ?? "");
  }
  if (type === "tool_use" || type === "tool_call") {
    const tool = textValue(payload.tool).trim() || "tool";
    const input = recordValue(payload.input);
    return input ? stringify(input) || tool : tool;
  }
  if (type === "tool_result" || type === "result") return textValue(payload.output || payload.content || payload.delta).trim();
  return textValue(payload.content || payload.delta || payload.status || payload.tool || payload.summary).trim();
}

export function parseActionProposal(payload: Record<string, unknown>): AgentSessionActionProposal | undefined {
  const proposal = recordValue(payload.action_proposal ?? payload.proposal);
  if (!proposal) return undefined;
  const action = textValue(proposal.action).trim();
  const nestedPayload = recordValue(proposal.payload);
  if (!action || !nestedPayload) return undefined;
  return {
    proposalEventId: textValue(proposal.proposal_event_id).trim(),
    proposalId: textValue(proposal.proposal_id).trim(),
    proposalDigest: textValue(proposal.proposal_digest).trim(),
    revision: Number(proposal.revision || 1),
    action,
    requestedAction: textValue(proposal.requested_action || proposal.action).trim(),
    payload: nestedPayload,
    reason: textValue(proposal.reason).trim(),
    confidence: textValue(proposal.confidence).trim(),
    valid: proposal.valid !== false,
    validationError: textValue(proposal.validation_error).trim(),
  };
}

function parsePlanOptions(value: unknown): AgentSessionPlanOption[] {
  const rawOptions = Array.isArray(value) ? value : [];
  return rawOptions.flatMap((item): AgentSessionPlanOption[] => {
    const option = recordValue(item);
    if (!option) return [];
    const id = textValue(option.id).trim();
    const label = textValue(option.label).trim();
    if (!id || !label) return [];
    const submitDetails = recordValue(option.submit_details);
    const submitPayload = recordValue(option.submit_payload);
    const members = Array.isArray(submitDetails?.members)
      ? submitDetails.members.flatMap((item): string[] => {
        const member = recordValue(item);
        const role = textValue(member?.role || member?.member_id).trim();
        return role ? [role] : [];
      })
      : [];
    return [{
      id,
      label,
      description: textValue(option.description).trim(),
      recommended: option.recommended === true,
      submitAction: textValue(option.submit_action).trim(),
      submitMode: (
        ["apply", "continue", "propose"].includes(textValue(option.submit_mode))
          ? textValue(option.submit_mode) as "apply" | "continue" | "propose"
          : undefined
      ),
      submitDetails: submitDetails ? {
        templateId: textValue(submitDetails.template_id).trim(),
        templateName: textValue(submitDetails.template_name).trim(),
        taskId: textValue(submitPayload?.task_id).trim(),
        objective: textValue(submitPayload?.objective).trim(),
        memberCount: Number(submitDetails.member_count || members.length || 0),
        roles: members,
        maxRounds: Number(submitDetails.max_rounds || 0),
        roundPolicy: textValue(submitDetails.round_policy).trim(),
        profiles: Array.isArray(submitDetails.profiles)
          ? submitDetails.profiles.flatMap((item) => {
            const profile = recordValue(item);
            if (!profile) return [];
            return [{
              memberId: textValue(profile.member_id).trim(),
              channelRole: textValue(profile.channel_role).trim(),
              profileId: textValue(profile.profile_id).trim(),
              displayName: textValue(profile.display_name).trim(),
            }];
          })
          : [],
        mode: textValue(submitDetails.mode).trim(),
        engineMode: textValue(submitDetails.engine_mode).trim(),
        routingStrategy: textValue(submitDetails.routing_strategy).trim(),
        firstPassReplyCount: Number(
          submitDetails.first_pass_reply_count || 0
        ),
        routeId: textValue(submitDetails.route_id).trim(),
        family: textValue(submitDetails.family).trim(),
        kind: textValue(submitDetails.kind).trim(),
        tier: textValue(submitDetails.tier).trim(),
        topology: textValue(submitDetails.topology).trim(),
        writerRoles: Array.isArray(submitDetails.writer_roles)
          ? submitDetails.writer_roles.map(textValue).filter(Boolean)
          : [],
        verifyRoles: Array.isArray(submitDetails.verify_roles)
          ? submitDetails.verify_roles.map(textValue).filter(Boolean)
          : [],
        laneCount: Number(submitDetails.lane_count || 0),
        inputKeys: Array.isArray(submitDetails.input_keys)
          ? submitDetails.input_keys.map(textValue).filter(Boolean)
          : [],
        preflight: textValue(submitDetails.preflight).trim(),
        outputProfile: textValue(submitDetails.output_profile).trim(),
      } : undefined,
    }];
  });
}

export function parsePlanRequest(payload: Record<string, unknown>): AgentSessionPlanRequest | undefined {
  const request = recordValue(payload.plan_request);
  if (!request) return undefined;
  const requestEventId = textValue(request.request_event_id).trim();
  const requestId = textValue(request.request_id).trim();
  const rawQuestions = Array.isArray(request.questions)
    ? request.questions
    : [request];
  let questions = rawQuestions.flatMap((item, index) => {
    const rawQuestion = recordValue(item);
    if (!rawQuestion) return [];
    const id = textValue(
      rawQuestion.id
      || rawQuestion.question_id
      || (index === 0 ? request.question_id || request.id : ""),
    ).trim();
    const question = textValue(
      rawQuestion.question
      || rawQuestion.text
      || (index === 0 ? request.question : ""),
    ).trim();
    const header = textValue(
      rawQuestion.header
      || (index === 0 ? request.header : ""),
    ).trim() || `Question ${index + 1}`;
    const options = parsePlanOptions(
      rawQuestion.options
      || (index === 0 ? request.options : []),
    );
    if (!id || !question) return [];
    return [{
      id,
      header,
      question,
      options,
      allowOther: rawQuestion.allow_other !== false,
    }];
  });
  const validationError = textValue(request.validation_error).trim();
  if (!questions.length && request.valid === false && validationError) {
    const rawQuestion = recordValue(rawQuestions[0]) || request;
    questions = [{
      id: textValue(
        rawQuestion.id
        || rawQuestion.question_id
        || request.question_id
        || request.id
        || "invalid-plan",
      ).trim(),
      header: textValue(rawQuestion.header || request.header).trim() || "Plan",
      question: "This Plan draft needs revision before it can be submitted.",
      options: parsePlanOptions(rawQuestion.options || request.options || []),
      allowOther: rawQuestion.allow_other !== false,
    }];
  }
  const primaryQuestion = questions[0];
  if (!requestEventId || !requestId || !primaryQuestion) return undefined;
  return {
    requestEventId,
    requestId,
    requestDigest: textValue(request.request_digest).trim(),
    revision: Math.max(1, Number(request.revision || 1)),
    header: textValue(request.header).trim() || "Plan",
    subjectType: (
      ["channel_setup", "clarification", "task_create", "task_workflow"].includes(textValue(request.subject_type))
        ? textValue(request.subject_type) as "channel_setup" | "clarification" | "task_create" | "task_workflow"
        : undefined
    ),
    questionId: primaryQuestion.id,
    question: primaryQuestion.question,
    options: primaryQuestion.options,
    allowOther: primaryQuestion.allowOther,
    questions,
    reason: textValue(request.reason).trim(),
    valid: (
      request.valid !== false
      && questions.length >= 1
      && questions.length <= 3
      && questions.every((item) => (
        item.options.length >= 2 && item.options.length <= 3
      ))
    ),
    validationError,
    backend: textValue(request.backend).trim(),
    providerSessionId: textValue(request.provider_session_id).trim(),
    submitAction: textValue(request.submit_action).trim(),
    submitMode: (
      ["apply", "continue", "propose"].includes(textValue(request.submit_mode))
        ? textValue(request.submit_mode) as "apply" | "continue" | "propose"
        : undefined
    ),
    submitLabel: textValue(request.submit_label).trim(),
    configDigest: textValue(request.config_digest).trim(),
  };
}

export function parsePlanResponse(
  payload: Record<string, unknown>,
  eventId = "",
): AgentSessionPlanResponse | undefined {
  const response = recordValue(payload.plan_response) ?? payload;
  const requestEventId = textValue(response.request_event_id).trim();
  const requestId = textValue(response.request_id).trim();
  const rawAnswers = Array.isArray(response.answers)
    ? response.answers
    : [response];
  const answers = rawAnswers.flatMap((item) => {
    const row = recordValue(item);
    if (!row) return [];
    const questionId = textValue(row.question_id).trim();
    const optionId = textValue(row.option_id).trim();
    const answer = textValue(row.answer).trim();
    return questionId && optionId && answer
      ? [{ questionId, optionId, answer }]
      : [];
  });
  const primaryAnswer = answers[0];
  if (!requestEventId || !requestId || !primaryAnswer) return undefined;
  return {
    requestEventId,
    requestId,
    revision: Math.max(1, Number(response.revision || 1)),
    questionId: primaryAnswer.questionId,
    optionId: primaryAnswer.optionId,
    answer: primaryAnswer.answer,
    answers,
    answerEventId: eventId,
  };
}

export function eventSourceRefs(event: EventRecord, refs?: Record<string, unknown> | null): Record<string, unknown> {
  return {
    ...(refs ?? {}),
    schema_version: AGENT_UI_EVENT_SCHEMA_VERSION,
    source_event_id: event.id || "",
    source_event_seq: event.seq ?? 0,
    source_event_type: event.type,
    task_id: event.task_id || textValue(refs?.task_id),
  };
}

export function agentStreamCoalesceKey(event: EventRecord): string {
  const payload = recordValue(event.payload) ?? {};
  return [
    event.type,
    textValue(payload.project_id),
    textValue(payload.channel_id),
    textValue(payload.conversation_id),
    textValue(payload.thread_id || payload.thread_key),
    textValue(payload.run_id || payload.turn_id || payload.provider_run_id),
    textValue(payload.part_id || payload.message_type || payload.type || "text"),
  ].join("|");
}

export function coalesceAgentStreamEvents(left: EventRecord, right: EventRecord): EventRecord {
  const leftPayload = recordValue(left.payload) ?? {};
  const rightPayload = recordValue(right.payload) ?? {};
  const leftText = textValue(leftPayload.delta ?? leftPayload.content ?? leftPayload.text);
  const rightText = textValue(rightPayload.delta ?? rightPayload.content ?? rightPayload.text);
  const content = `${leftText}${rightText}`;
  return {
    ...right,
    payload: {
      ...leftPayload,
      ...rightPayload,
      delta: content,
      content,
      coalesced_count: Number(leftPayload.coalesced_count || 1) + 1,
      first_source_event_id: textValue(leftPayload.first_source_event_id || left.id),
      source_event_ids: [
        ...toStringArray(leftPayload.source_event_ids),
        left.id,
        right.id,
      ].filter(Boolean),
    },
  };
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => textValue(item)).filter(Boolean);
}
