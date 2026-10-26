import { consumeDashboardLaunchToken } from "../src/api/dashboardLaunch.js";

function check(hash: string, initial: string | undefined, expected: string | undefined, url: string): void {
  const values = new Map<string, string>();
  if (initial) values.set("zf.webActionToken", initial);
  let replaced = "";
  const browser = {
    location: { hash, pathname: "/projects/test", search: "?tab=kanban" },
    localStorage: { setItem: (key: string, value: string) => values.set(key, value) },
    history: { state: { retained: true }, replaceState: (_state: unknown, _unused: string, value: string) => { replaced = value; } },
  } as unknown as Pick<Window, "location" | "localStorage" | "history">;
  consumeDashboardLaunchToken(browser);
  if (values.get("zf.webActionToken") !== expected || replaced !== url) {
    throw new Error("launch credential or URL cleanup mismatch");
  }
}

check("#zf-action-token=launch%2Btoken", "old", "launch+token", "/projects/test?tab=kanban");
check("#zf-action-token=new&panel=task", undefined, "new", "/projects/test?tab=kanban#panel=task");
check("#panel=task", "existing", "existing", "");
check("#zf-action-token=%E2%80%A2", "existing", "existing", "/projects/test?tab=kanban");
console.log("dashboardLaunch tests passed");
