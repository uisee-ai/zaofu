// Only consult a local action credential after the server identifies the Web session mode.
let localTokenEnabled = false;

export function localWebActionToken(): string {
  return localTokenEnabled ? window.localStorage.getItem("zf.webActionToken")?.trim() ?? "" : "";
}

export function consumeDashboardLaunchToken(browser: Pick<Window, "location" | "localStorage" | "history">, sessionMode: string): void {
  localTokenEnabled = ["local_trusted", "remote_passcode", "token_required"].includes(sessionMode);
  if (sessionMode === "controller_session") browser.localStorage.removeItem("zf.webActionToken");
  const fragment = new URLSearchParams(browser.location.hash.slice(1));
  const token = fragment.get("zf-action-token");
  if (!token) return;
  if (localTokenEnabled && /^[\x21-\x7e]+$/.test(token)) {
    browser.localStorage.setItem("zf.webActionToken", token);
  }
  fragment.delete("zf-action-token");
  const remaining = fragment.toString();
  browser.history.replaceState(browser.history.state, "",
    `${browser.location.pathname}${browser.location.search}${remaining ? `#${remaining}` : ""}`);
}
