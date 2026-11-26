import { lazy, Suspense, useEffect, useState, type ComponentProps } from "react";
import { getTaskReadingRecord } from "../api/client";
import type { Task } from "../api/types";

const LazyChannelPage = lazy(() => import("../components/channel/ChannelPage").then((module) => ({
  default: module.ChannelPage,
})));
const LazyTaskReader = lazy(() => import("../components/kanban/TaskReader").then((module) => ({
  default: module.TaskReader,
})));
const LazyOrchestratorPanel = lazy(() => import("../components/orchestrator/OrchestratorPanel").then((module) => ({
  default: module.OrchestratorPanel,
})));
const LazyProjectionPage = lazy(() => import("../components/projection/ProjectionPage").then((module) => ({
  default: module.ProjectionPage,
})));
const LazyTracesPage = lazy(() => import("../components/traces/TracesPage").then((module) => ({
  default: module.TracesPage,
})));

function RouteLoading({ label }: { label: string }) {
  return (
    <section aria-busy="true" className="subsection route-loading">
      <p className="muted">Loading {label}...</p>
    </section>
  );
}

export function ChannelRoute(props: ComponentProps<typeof LazyChannelPage>) {
  return <Suspense fallback={<RouteLoading label="channel" />}><LazyChannelPage {...props} /></Suspense>;
}

export function TaskReaderRoute(props: ComponentProps<typeof LazyTaskReader>) {
  const { projectId, selectedTaskId, detail } = props;
  const [record, setRecord] = useState<Task | null>(null);
  const hasDetail = Boolean(detail);
  useEffect(() => {
    if (!projectId || !selectedTaskId || hasDetail) return;
    const controller = new AbortController();
    void getTaskReadingRecord(selectedTaskId, projectId, controller.signal)
      .then(task => { if (!controller.signal.aborted && task.id === selectedTaskId) setRecord(task); })
      .catch(() => { /* The full detail request owns the reader's error and retry. */ });
    return () => controller.abort();
  }, [projectId, selectedTaskId, hasDetail]);
  // Board rows can carry live progress while the lightweight record supplies
  // the canonical contract. The full detail takes precedence once available.
  const task = record ? { ...record, ...props.task, contract: record.contract } : props.task;
  return <Suspense fallback={<p role="status">正在打开任务…</p>}><LazyTaskReader {...props} task={task} /></Suspense>;
}

export function OrchestratorRoute(props: ComponentProps<typeof LazyOrchestratorPanel>) {
  return <Suspense fallback={<RouteLoading label="zaofu" />}><LazyOrchestratorPanel {...props} /></Suspense>;
}

export function ProjectionRoute(props: ComponentProps<typeof LazyProjectionPage>) {
  return <Suspense fallback={<RouteLoading label="workspace view" />}><LazyProjectionPage {...props} /></Suspense>;
}

export function TracesRoute(props: ComponentProps<typeof LazyTracesPage>) {
  return <Suspense fallback={<RouteLoading label="traces" />}><LazyTracesPage {...props} /></Suspense>;
}
