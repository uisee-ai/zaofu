// Browser integration harness for production components. All API routes are
// intercepted by the spec; no project runtime or provider is started.
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { PlanApprovalPanel } from "../src/components/delivery-trace/PlanApprovalPanel";
import { WorkflowProposalPage } from "../src/components/workflow/WorkflowProposalPage";
import { BoardWorkbench } from "../src/components/kanban/BoardWorkbench";
import { BOARD_COLUMNS, taskColumn } from "../src/components/kanban/board";
import type { BoardColumnId } from "../src/components/kanban/board";
import type { Task } from "../src/api/types";
import "../src/styles.css";
import "../src/cockpit.css";

const task = { id: "TASK-1", title: "Ship checkout", status: "in_progress", assigned_to: "dev-1", priority: 1,
  created_at: "2026-10-26T00:00:00Z", retry_count: 3, blocked_reason: "Waiting for owner", skills_required: ["secret-skill"],
  workflow_badges: [{ kind: "verify", label: "Internal verify waiting", tone: "warn" }],
  fanout: { fanout_id: "FANOUT-1", lane_id: "internal-lane", affinity_tag: "internal-affinity", progress: { done: 1, total: 2 } },
  route_summary: { empty: false, summary: "internal-route -> internal-judge", status: "running" },
} as unknown as Task;
const tasks = [task,
  { ...task, id: "TASK-2", title: "Cancelled work", status: "cancelled", effective_terminal: true },
  { ...task, id: "TASK-3", title: "Queued work", blocked_reason: "fanout_queue:historical", attention: { required: false } } as Task,
];

function Fixture() {
  const surface = new URLSearchParams(location.search).get("surface") || "workflow";
  const [project, setProject] = useState("a");
  const [mode, setMode] = useState<"board" | "list">("board");
  const [selected, setSelected] = useState<string | null>(null);
  const [actions, setActions] = useState<unknown[]>([]);
  const noop = () => {};
  return <>
    <button onClick={() => setProject(project === "a" ? "b" : "a")}>Switch project</button>
    <output data-testid="actions">{JSON.stringify(actions)}</output>
    {surface === "inbox" ? <PlanApprovalPanel projectId={project} autoOpenPlanId={new URLSearchParams(location.search).get("plan")} /> : surface === "board" ? <BoardWorkbench
      actionReady actionState="ready" actionResult={null} activeFanouts={[]} agents={[]} assignees={["dev-1"]} assigneeFilter="all"
      filteredTasks={tasks} mutationEnabled={false} onMoveTaskStatus={noop} onOpenFanout={noop} onOpenTask={setSelected}
      onSaveToken={noop} onUnlockSession={async () => ({ ok: true })} passcodeRequired={false} priorityFilter="all" quickFilter="all"
      selectedTaskId={selected} setAssigneeFilter={noop} setPriorityFilter={noop} setQuickFilter={noop} setSkillFilter={noop}
      showTokenRow={false} skillFilter="all" skills={[]} setStatusFilter={noop} setTextFilter={noop} setViewMode={setMode}
      statusFilter="all" textFilter="" totalTaskCount={tasks.length} viewMode={mode}
      tasksByColumn={Object.fromEntries(BOARD_COLUMNS.map((column) => [column.id, tasks.filter((task) => taskColumn(task) === column.id)])) as Record<BoardColumnId, Task[]>}
    /> : <WorkflowProposalPage actionReady actionState="ready" projectId={project} onOpenProjection={noop}
      onAction={async (action, payload) => { setActions((items) => [...items, { project, action, payload }]); return { ok: true }; }} />}
  </>;
}
createRoot(document.getElementById("root")!).render(<React.StrictMode><Fixture /></React.StrictMode>);
