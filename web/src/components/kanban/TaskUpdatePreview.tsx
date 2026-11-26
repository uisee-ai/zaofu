import type { ActionPresentation } from "../agent-session/actionPresentation";
import { taskUpdateFields } from "../agent-session/previewRegistry";
import { recordValue } from "../../app/shared";

const labels: Record<string, string> = {
  task_id: "任务", title: "标题", status: "状态", priority: "优先级", assigned_to: "执行者",
  blocked_reason: "阻塞原因", behavior: "任务目标", verification: "验证方式",
  acceptance: "验收条件", acceptance_criteria: "验收条目", blocked_by: "依赖任务", skills: "技能",
  skills_required: "技能", owner: "执行者", evidence: "证据", scope: "范围", exclusions: "排除范围",
};

function formatValue(key: string, value: unknown): string {
  if (Array.isArray(value)) {
    return value.map(item => formatValue(key, item)).join("\n") || "（清空）";
  }
  const record = recordValue(value);
  if (key === "acceptance_criteria" && record) {
    const label = record.text || record.statement || record.criterion || record.description || record.acceptance;
    if (typeof label === "string" && label) return label;
  }
  return typeof value === "object" && value !== null ? JSON.stringify(value) : String(value ?? "") || "（清空）";
}

export function taskUpdatePresentation(payload: Record<string, unknown>): ActionPresentation {
  const contract = payload.contract;
  const acceptance = contract && typeof contract === "object"
    && ("acceptance" in contract || "acceptance_criteria" in contract);
  return {
    title: "更新任务", confirmLabel: "确认更新", busyLabel: "正在更新",
    completedLabel: acceptance ? "验收条件已更新" : "任务已更新",
  };
}

export function TaskUpdatePreview({ payload }: { payload: Record<string, unknown> }) {
  const rows = taskUpdateFields(payload);
  return <dl className="reader-update-preview">
    {rows.map(([key, value]) => <div key={key}><dt>{labels[key] || key}</dt><dd>{formatValue(key, value)}</dd></div>)}
  </dl>;
}
