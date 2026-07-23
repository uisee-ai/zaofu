import type { PageId } from "./sharedTypes";

const PAGE_TITLES: Record<PageId, string> = {
  project: "Overview",
  inbox: "Inbox",
  channels: "Channels",
  board: "Tasks",
  triage: "Triage",
  observability: "Observability",
  events: "Events",
  agents: "Agents",
  automations: "Automations",
  backlogs: "Backlogs",
  workdirs: "Workdirs",
  skills: "Skills",
  traces: "Event Traces",
  delivery: "Delivery",
  "goal-coverage": "Goal Coverage",
  "delivery-trace": "Runs",
  "delivery-graph": "Graph",
  "behavior-loop": "Loop",
  "control-room": "Control (retired)",
  diagnostics: "Diagnostics",
  candidates: "Candidates",
  fanouts: "Fanouts",
  runs: "Runs",
  archives: "Archives",
  runtime: "Runtime",
  settings: "Settings",
  task: "Task",
};

export function pageTitle(page: PageId): string {
  return PAGE_TITLES[page] ?? page;
}
