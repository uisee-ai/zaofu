import type { PageId } from "./sharedTypes";
import { uiText } from "../settings/uiText";

const PAGE_TITLES: Record<PageId, string> = {
  project: "Overview",
  inbox: "Inbox",
  channels: "Channels",
  board: "Tasks",
  workflows: "Workflows",
  triage: "Triage",
  "issue-triage": "Triage",
  observability: "Observability",
  events: "Events",
  agents: "Agents",
  automations: "Automations",
  backlogs: "Backlogs",
  workdirs: "Workdirs",
  skills: "Skills",
  traces: "Traces",
  delivery: "Delivery",
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
  return uiText(PAGE_TITLES[page] ?? page);
}
