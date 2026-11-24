import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { TaskReader } from "../src/components/kanban/TaskReader";
import type { Task, TaskDetail as Detail } from "../src/api/types";
import "../src/styles.css";
import "../src/styles/28-workbench.css";

function Fixture() {
  const [stale, setStale] = useState(false);
  const task = { id: "TASK-HANDOFF", title: "核对任务接力", status: "in_progress", assigned_to: "verify-2",
    phase: "implement", active_dispatch_id: "dispatch-current", blocked_by: [], skills_required: [] } as unknown as Task;
  const detail = { task, events: [], contract: {}, evidence: {}, links: {}, briefing: {}, diagnostics: {},
    execution_route: { current_stage: "verify", current_stage_label: "verify", linear: [], dag: { nodes: [], edges: [] },
      swimlanes: [], source_events: [], empty: true },
    task_run_panel: { status: "in_progress", current_stage: "verify", route_summary: { current_stage: "verify" } },
    handoff_summary: { schema_version: "handoff-summary.v1", task_id: task.id, objective: task.title,
      current_state: "in_progress", current_stage: "verify", owner: { role: "verify", instance_id: "verify-2" },
      completed: [{ kind: "admitted_result", summary: "verify · generation 1 · failed" }],
      next_required_event: "task.pipeline.verify.completed", next_required_action: "等待当前阶段提交并接纳结果",
      pipeline: { status: "current", operation_status: "running", current_stage: "verify", pipeline_stage: "verify_active", workflow_run_id: "run-current",
        attempt_id: "attempt-current", dispatch_id: "dispatch-current", target_ref: "target-current",
        workspace: stale ? { status: "unavailable", reason: "task workspace metadata does not match the current dispatch" }
          : { status: "bound", path: "/custom-state/execution/tasks/TASK-HANDOFF/g1/project" } },
      input_recovery: { status: stale ? "rebind_required" : "bound", required_visible: 3, historical_missing: 1,
        current_memory: "unknown", checkpoint: { status: "captured" } } },
  } as unknown as Detail;
  return <><button onClick={() => setStale(!stale)}>切换过期绑定</button>
    <TaskReader projectId="handoff" selectedTaskId={task.id} task={task} detail={detail}
      actionReady={false} actionResult={null} actionState="" events={[]} diff={null} loadError={null}
      onAction={() => { throw new Error("Read-only journey issued an action"); }} onBackToBoard={() => {}}
      onOpenProjection={() => {}} skillsSummary={null}
      timeline={null} timelineError={null} timelineLoading={false} /></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
