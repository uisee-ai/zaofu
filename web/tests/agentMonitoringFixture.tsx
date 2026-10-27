import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { useAgentMonitoring } from "../src/components/agent-view/useAgentMonitoring";
import type { RecentEvent } from "../src/api/types";

function Fixture() {
  const [seq, setSeq] = useState(1);
  const events = [{ id: `event-${seq}`, seq, type: "task.created" }] as RecentEvent[];
  const state = useAgentMonitoring("fixture", events, "live");
  return <main>
    <button onClick={() => setSeq(value => value + 1)}>Event</button>
    <span role="status">{state.loading ? "Refreshing" : "Ready"}</span>
    {state.error && <p role="alert">{state.error}</p>}
    <output>{state.data ? JSON.stringify(state.data.metrics_snapshot) : "No projection"}</output>
  </main>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
