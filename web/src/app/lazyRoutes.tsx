import { lazy, Suspense, type ComponentProps } from "react";

const LazyChannelPage = lazy(() => import("../components/channel/ChannelPage").then((module) => ({
  default: module.ChannelPage,
})));
const LazyOrchestratorPanel = lazy(() => import("../components/orchestrator/OrchestratorPanel").then((module) => ({
  default: module.OrchestratorPanel,
})));
const LazyProjectionPage = lazy(() => import("../components/projection/ProjectionPage").then((module) => ({
  default: module.ProjectionPage,
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

export function OrchestratorRoute(props: ComponentProps<typeof LazyOrchestratorPanel>) {
  return <Suspense fallback={<RouteLoading label="Kanban Agent" />}><LazyOrchestratorPanel {...props} /></Suspense>;
}

export function ProjectionRoute(props: ComponentProps<typeof LazyProjectionPage>) {
  return <Suspense fallback={<RouteLoading label="workspace view" />}><LazyProjectionPage {...props} /></Suspense>;
}
