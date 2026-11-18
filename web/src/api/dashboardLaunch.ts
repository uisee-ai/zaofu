// An authorization link may be rewritten by the router before the session API replies.
let pendingLaunchToken: string | null = null;
let localTokenEnabled = false;

export function captureDashboardLaunchToken(browser: Pick<Window, "location" | "history">): void {
  const fragment = new URLSearchParams(browser.location.hash.slice(1));
  if (!fragment.has("zf-action-token")) return;
  const token = fragment.get("zf-action-token") ?? "";
  pendingLaunchToken = /^[\x21-\x7e]+$/.test(token) ? token : null;
  fragment.delete("zf-action-token");
  const remaining = fragment.toString();
  browser.history.replaceState(browser.history.state, "",
    `${browser.location.pathname}${browser.location.search}${remaining ? `#${remaining}` : ""}`);
}

export function configureDashboardSession(storage: Storage, sessionMode: string): void {
  localTokenEnabled = ["local_trusted", "remote_passcode", "token_required"].includes(sessionMode);
  if (sessionMode === "controller_session") storage.removeItem("zf.webActionToken");
  if (localTokenEnabled && pendingLaunchToken) storage.setItem("zf.webActionToken", pendingLaunchToken);
  pendingLaunchToken = null;
}

export function localWebActionToken(): string {
  return localTokenEnabled ? window.localStorage.getItem("zf.webActionToken")?.trim() ?? "" : "";
}
