import { localWebActionToken } from "../../api/dashboardLaunch.js";
import type {
  TerminalAttachmentMode,
  TerminalAttachmentTicket,
  TerminalMutationResponse,
  TerminalProvider,
  TerminalSessionsPage,
} from "./types";

function projectTerminalPrefix(projectId: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/terminal-sessions`;
}

function actionHeaders(): Record<string, string> {
  // This is the dashboard's existing mutation credential. Attachment tickets
  // returned by these calls remain memory-only and never enter storage/URLs.
  const token = localWebActionToken();
  return token
    ? { "X-ZF-Web-Token": token, Authorization: `Bearer ${token}` }
    : {};
}

async function responseJson<T>(response: Response): Promise<T> {
  const value = await response.json() as Record<string, unknown>;
  if (!response.ok) {
    const detail = value.detail;
    const nested = typeof detail === "object" && detail !== null
      ? detail as Record<string, unknown>
      : null;
    throw new Error(String(value.reason ?? nested?.reason ?? value.status ?? `request failed: ${response.status}`));
  }
  return value as T;
}

export async function fetchTerminalSessions(projectId: string): Promise<TerminalSessionsPage> {
  const response = await fetch(projectTerminalPrefix(projectId), {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  return responseJson<TerminalSessionsPage>(response);
}

export async function createTerminalSession(
  projectId: string,
  provider: TerminalProvider,
  slot: string,
  title: string,
): Promise<TerminalMutationResponse> {
  const response = await fetch(projectTerminalPrefix(projectId), {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...actionHeaders(),
    },
    body: JSON.stringify({ provider, slot, title }),
  });
  return responseJson<TerminalMutationResponse>(response);
}

export async function renameTerminalSession(
  projectId: string,
  sessionId: string,
  title: string,
): Promise<TerminalMutationResponse> {
  const response = await fetch(
    `${projectTerminalPrefix(projectId)}/${encodeURIComponent(sessionId)}/rename`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...actionHeaders(),
      },
      body: JSON.stringify({ title }),
    },
  );
  return responseJson<TerminalMutationResponse>(response);
}

export async function stopTerminalSession(
  projectId: string,
  sessionId: string,
): Promise<TerminalMutationResponse> {
  const response = await fetch(
    `${projectTerminalPrefix(projectId)}/${encodeURIComponent(sessionId)}/stop`,
    {
      method: "POST",
      headers: { Accept: "application/json", ...actionHeaders() },
    },
  );
  return responseJson<TerminalMutationResponse>(response);
}

export async function issueTerminalAttachment(
  projectId: string,
  sessionId: string,
  mode: TerminalAttachmentMode,
  geometry: { cols: number; rows: number },
  takeover = false,
): Promise<TerminalAttachmentTicket> {
  const suffix = takeover ? "takeover" : "attachments";
  const response = await fetch(
    `${projectTerminalPrefix(projectId)}/${encodeURIComponent(sessionId)}/${suffix}`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...actionHeaders(),
      },
      body: JSON.stringify({ mode, ...geometry }),
    },
  );
  return responseJson<TerminalAttachmentTicket>(response);
}

export function terminalWebSocketUrl(
  projectId: string,
  sessionId: string,
  mode: TerminalAttachmentMode,
): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${projectTerminalPrefix(projectId)}/${encodeURIComponent(sessionId)}/${mode}`;
}
