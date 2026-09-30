import { useSettingsPreferences } from "../settings/useSettingsPreferences";
import { uiText } from "../settings/uiText";
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
const LazyTracesPage = lazy(() => import("../components/traces/TracesPage").then((module) => ({
  default: module.TracesPage,
})));

function RouteLoading({ label }: { label: string }) {
  useSettingsPreferences();
  return (
    <section aria-busy="true" className="subsection route-loading">
      <p className="muted">{uiText("Loading")} {label}...</p>
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

export function TracesRoute(props: ComponentProps<typeof LazyTracesPage>) {
  return <Suspense fallback={<RouteLoading label="traces" />}><LazyTracesPage {...props} /></Suspense>;
}
