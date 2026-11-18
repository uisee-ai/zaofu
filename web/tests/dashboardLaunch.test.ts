import { captureDashboardLaunchToken, configureDashboardSession } from "../src/api/dashboardLaunch.js";

function check(hash: string, initial: string | undefined, expected: string | undefined, url: string, sessionMode = "token_required"): void {
  const values = new Map<string, string>();
  if (initial) values.set("zf.webActionToken", initial);
  let replaced = "";
  const browser = {
    location: { hash, pathname: "/projects/test", search: "?tab=kanban" },
    localStorage: { removeItem: (key: string) => values.delete(key), setItem: (key: string, value: string) => values.set(key, value) },
    history: { state: { retained: true }, replaceState: (_state: unknown, _unused: string, value: string) => { replaced = value; } },
  } as unknown as Pick<Window, "location" | "localStorage" | "history">;
  captureDashboardLaunchToken(browser);
  if (values.get("zf.webActionToken") !== initial) throw new Error("unverified launch credential was persisted");
  browser.location.hash = "";
  configureDashboardSession(browser.localStorage, sessionMode);
  if (values.get("zf.webActionToken") !== expected || replaced !== url) {
    throw new Error("launch credential or URL cleanup mismatch");
  }
}

check("#zf-action-token=launch%2Btoken", "old", "launch+token", "/projects/test?tab=kanban");
check("#zf-action-token=new&panel=task", undefined, "new", "/projects/test?tab=kanban#panel=task");
check("#panel=task", "existing", "existing", "");
check("#zf-action-token=%E2%80%A2", "existing", "existing", "/projects/test?tab=kanban");
check("#zf-action-token=unused&panel=task", "stale", undefined, "/projects/test?tab=kanban#panel=task", "controller_session");
check("", "stale", undefined, "", "controller_session");
check("#zf-action-token=unused", "saved", "saved", "/projects/test?tab=kanban", "read_only");
console.log("dashboardLaunch tests passed");
