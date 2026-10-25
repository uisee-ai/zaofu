import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "@fontsource/geist-mono/400.css";
import "@fontsource/geist-mono/700.css";
import { App } from "./app/App";
import { consumeDashboardLaunchToken } from "./api/dashboardLaunch";
import "./styles.css";
import "./cockpit.css";

const AgentSessionFixturePage = React.lazy(() => (
  import("./components/agent-session/AgentSessionFixturePage").then((module) => ({
    default: module.AgentSessionFixturePage,
  }))
));

const root = document.getElementById("root");
consumeDashboardLaunchToken(window);

if (!root) {
  throw new Error("missing #root");
}

// Dev-only render harness: `/?fixture=agent-session` mounts the timeline with
// deterministic fixture data (no backend) for visual verification.
const fixture = new URLSearchParams(window.location.search).get("fixture");

createRoot(root).render(
  <React.StrictMode>
    {fixture === "agent-session" ? (
      <React.Suspense fallback={null}>
        <AgentSessionFixturePage />
      </React.Suspense>
    ) : <App />}
  </React.StrictMode>,
);
