import type { EventRecord, Task, TaskDetail } from "../../api/types";
import { asRecord, textValue } from "../../app/shared";
import { taskIsEffectivelyTerminal } from "../../lib/task-display";
import { taskStatus } from "./taskViewModel";

const stageNames: Record<string, string> = {
  impl: "实现", implement: "实现", implementation: "实现", impl_rework: "返工",
  verify: "验证", acceptance_review: "验收评审", integration: "集成",
  review: "评审", test: "测试", judge: "裁决",
};
const stateNames: Record<string, string> = {
  dependency_blocked: "等待依赖", external_gate_waiting: "待你补充证据", admission_blocked: "准入受阻",
  replan_requested: "等待重新规划", integrated: "集成已通过", blocked: "执行受阻", ready: "等待调度",
  done: "已完成", cancelled: "已取消", archived: "已归档", superseded: "已被替代",
  in_progress: "进行中", review: "评审阶段", testing: "测试阶段", unknown: "执行状态待确认",
};

function pipelineState(pipeline: Record<string, unknown>, activeDispatch: unknown) {
  const stage = textValue(pipeline.pipeline_stage || pipeline.current_stage);
  const operationStage = textValue(pipeline.current_stage);
  const operationName = stageNames[operationStage] || operationStage;
  const operationStatus = pipeline.operation_status;
  if (operationStatus === "suspended") return { label: `${operationName}已暂停` };
  if (operationStatus === "requested" || operationStatus === "reserved") return { label: `等待${operationName}` };
  // Older dashboards expose dispatch and stage but omit the operation status.
  const dispatched = pipeline.dispatch_id && stage === operationStage + "_active";
  if (!operationStatus && dispatched) return { label: `${operationName}已派发` };
  if (operationStatus === "running") {
    const owner = asRecord(pipeline.owner);
    const bound = pipeline.dispatch_id && (!activeDispatch || pipeline.dispatch_id === activeDispatch);
    return { label: `${operationName}中`, worker: bound ? textValue(owner.instance_id) : undefined };
  }
  const phase = stage.replace(/_(ready|active)$/, "");
  if (phase !== stage) return { label: `等待${stageNames[phase] || phase}` };
  return { label: stateNames[stage] || stageNames[stage] || stage || stateNames.unknown };
}

export function taskExecution(task: Task, detail: TaskDetail | null): { label: string; worker?: string; manualEvidence?: boolean } {
  const status = taskStatus(task);
  if (taskIsEffectivelyTerminal(task)) return { label: stateNames[status] || status };
  if (!detail) return { label: "正在读取执行进度" };
  const handoff = detail.handoff_summary;
  const pipeline = handoff?.pipeline;
  if (handoff?.error || (!pipeline && detail.task_run_panel?.error) || (pipeline && pipeline.status !== "current")) return { label: stateNames.unknown };
  if (pipeline) {
    const state = pipelineState({ ...pipeline, owner: pipeline.owner ?? handoff.owner }, detail.task.active_dispatch_id);
    return { ...state, manualEvidence: pipeline.pipeline_stage === "external_gate_waiting" };
  }
  const op = detail.task_run_panel?.active_operation;
  const dispatch = detail.task.active_dispatch_id;
  if (dispatch && op?.dispatch_id === dispatch && op.active && op.state === "in_progress") {
    return { label: "执行中", worker: textValue(op.instance_id) };
  }
  if (status === "backlog") {
    const binding = asRecord(detail.task.execution_binding);
    if (binding.request_id && !binding.workflow_run_id) return { label: "等待流程启动" };
    const run = binding.workflow_run_id || task.fanout?.workflow_run_id;
    const hasExecution = run || dispatch || handoff?.receipt?.delivery_status || op?.operation_id || detail.runs?.length;
    return { label: hasExecution ? "等待流程推进" : "未进入流程" };
  }
  return { label: stateNames[status] || status };
}

export function latestInvalidContract(events: EventRecord[]) {
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i];
    if (event.type === "task.contract.invalid") return event;
    if (event.type === "task.contract.update" || event.type === "task.dispatched") break;
  }
  return null;
}
