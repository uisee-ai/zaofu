import { useEffect, useState } from "react";
import { getTaskDetail, getTaskDiff, getTaskTimeline } from "../../api/client";
import type { SkillsSummary, TaskDetail, TaskDiff, TaskTimeline } from "../../api/types";
import type { ProjectionKind } from "../../app/sharedTypes";
import { HandoffBindingDetails } from "../kanban/HandoffBindingDetails";
import {
  ContractInvalidBanner, HandoffSummaryPanel, TaskActivityPanel, TaskAdvancedPanel,
  TaskEvidencePanel, TaskRunSummaryPanel,
} from "../kanban/TaskDetail";

interface Props {
  taskId: string;
  projectId: string;
  skills: SkillsSummary | null;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
}

export function TaskInspect(props: Props) {
  const [open, setOpen] = useState(false);
  return <details className="subsection" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>查看任务运行详情 · {props.taskId}</summary>
    {open && <InspectBody {...props} />}
  </details>;
}

function InspectBody({ taskId, projectId, skills, onOpenProjection }: Props) {
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [diff, setDiff] = useState<TaskDiff | null>(null);
  const [timeline, setTimeline] = useState<TaskTimeline | null>(null);
  const [error, setError] = useState("");
  const [timelineError, setTimelineError] = useState("");
  const [showEvidence, setShowEvidence] = useState(false);
  const [showActivity, setShowActivity] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    getTaskDetail(taskId, projectId, signal).then(setDetail)
      .catch(err => { if (!signal.aborted) setError(String(err)); });
    getTaskDiff(taskId, projectId, signal).then(setDiff)
      .catch(err => { if (!signal.aborted) setError(String(err)); });
    getTaskTimeline(taskId, projectId, signal).then(setTimeline)
      .catch(err => { if (!signal.aborted) setTimelineError(String(err)); });
    return () => controller.abort();
  }, [taskId, projectId]);

  return <>
    {error && <p role="alert">{error}</p>}
    {detail ? <>
      <ContractInvalidBanner task={detail.task} detail={detail} events={[]} />
      <HandoffSummaryPanel detail={detail} />
      <HandoffBindingDetails summary={detail.handoff_summary} onEvidence={() => setShowEvidence(true)} onActivity={() => setShowActivity(true)} />
      <TaskRunSummaryPanel task={detail.task} detail={detail} onOpenProjection={onOpenProjection} />
      <details open={showEvidence} onToggle={e => setShowEvidence(e.currentTarget.open)}><summary>输入与交付依据</summary>
        {showEvidence && <TaskEvidencePanel detail={detail} diff={diff} projectId={projectId} />}
      </details>
      <details open={showActivity} onToggle={e => setShowActivity(e.currentTarget.open)}><summary>执行路线与事件</summary>
        {showActivity && <TaskActivityPanel detail={detail} events={timeline?.timeline ?? detail.events}
          route={timeline?.execution_route ?? detail.execution_route ?? null} timelineLoading={!timeline && !timelineError}
          timelineError={timelineError} traceId={detail.trace_id || ""} onOpenProjection={onOpenProjection} />}
      </details>
      <TaskAdvancedPanel task={detail.task} detail={detail} skillsSummary={skills} />
    </> : !error && <p role="status">正在读取任务…</p>}
  </>;
}
