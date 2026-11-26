import { lazy, Suspense, type ComponentProps } from "react";

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
  return <Suspense fallback={<p role="status">正在打开任务…</p>}><LazyTaskReader {...props} /></Suspense>;
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
