// Toggle a leading "● " on the browser tab title while an agent session is
// working, so a backgrounded tab signals activity.
// ChatPage sets `document.title = "● " + base`). Single-owner by design —
// call it from ONE live-session surface to avoid two effects fighting over
// document.title. The base title is recovered by stripping any existing
// marker, so repeated toggles never accumulate dots.

import { useEffect } from "react";

export function useWorkingTitle(active: boolean): void {
  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const base = document.title.replace(/^●\s+/, "");
    document.title = active ? `● ${base}` : base;
    return () => {
      document.title = document.title.replace(/^●\s+/, "");
    };
  }, [active]);
}
