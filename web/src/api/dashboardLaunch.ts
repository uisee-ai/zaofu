/** Consume the operator launch link before React or API requests start. */
export function consumeDashboardLaunchToken(browser: Pick<Window, "location" | "localStorage" | "history">): void {
  const fragment = new URLSearchParams(browser.location.hash.slice(1));
  const token = fragment.get("zf-action-token");
  if (!token) return;
  if (/^[\x21-\x7e]+$/.test(token)) {
    browser.localStorage.setItem("zf.webActionToken", token);
  }
  fragment.delete("zf-action-token");
  const remaining = fragment.toString();
  browser.history.replaceState(browser.history.state, "",
    `${browser.location.pathname}${browser.location.search}${remaining ? `#${remaining}` : ""}`);
}
