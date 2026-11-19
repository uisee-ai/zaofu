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
  if (sessionMode === "local_account" || sessionMode === "controller_session") storage.removeItem("zf.webActionToken");
  if (localTokenEnabled && pendingLaunchToken) storage.setItem("zf.webActionToken", pendingLaunchToken);
  pendingLaunchToken = null;
}

export function localWebActionToken(): string {
  return localTokenEnabled ? window.localStorage.getItem("zf.webActionToken")?.trim() ?? "" : "";
}

/** A rejected session returns the user to the owning login flow, including writes from nested panels. */
export function watchDashboardLogin(managed: boolean): () => void {
  const original = window.fetch;
  window.fetch = async (...args) => {
    const response = await original.call(window, ...args);
    if (response.status === 401 && response.url && new URL(response.url).origin === window.location.origin
        && (response.headers.get("X-ZF-Login") === "required" || (managed && new URL(response.url).pathname.startsWith("/api/")))) {
      window.location.reload();
    }
    return response;
  };
  return () => { window.fetch = original; };
}
