import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "@fontsource-variable/source-serif-4";
import "@fontsource-variable/source-serif-4/wght-italic.css";
import "@fontsource/geist-mono/400.css";
import "@fontsource/geist-mono/700.css";
import { App } from "./app/App";
import { AgentSessionFixturePage } from "./components/agent-session/AgentSessionFixturePage";
import "./styles.css";
import "./cockpit.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("missing #root");
}

// Dev-only render harness: `/?fixture=agent-session` mounts the timeline with
// deterministic fixture data (no backend) for visual verification.
const fixture = new URLSearchParams(window.location.search).get("fixture");

createRoot(root).render(
  <React.StrictMode>
    {fixture === "agent-session" ? <AgentSessionFixturePage /> : <App />}
  </React.StrictMode>,
);
