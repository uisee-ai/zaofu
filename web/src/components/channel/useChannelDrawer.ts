import { useEffect, useState, type SetStateAction } from "react";

// Bind a shareable PRD reader to its project, Channel and visible thread.
export function useChannelDrawer<T extends string>(projectId: string, channelId: string, threadId: string) {
  const scope = JSON.stringify([projectId, channelId, threadId]);
  const fromLocation = (): T | null => {
    const params = new URLSearchParams(window.location.search);
    return params.get("page") === "channels" && params.get("project") === projectId
      && params.get("channel") === channelId && (!params.get("thread") || params.get("thread") === threadId)
      && params.get("preview") === "prd" ? "prd" as T : null;
  };
  const [selection, setSelection] = useState(() => ({ scope, drawer: fromLocation() }));
  if (selection.scope !== scope) setSelection({ scope, drawer: fromLocation() });
  const drawer = selection.scope === scope ? selection.drawer : fromLocation();
  useEffect(() => {
    const restore = () => setSelection({ scope, drawer: fromLocation() });
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [scope]);
  const setDrawer = (next: SetStateAction<T | null>) => {
    const value = typeof next === "function" ? next(drawer) : next;
    setSelection({ scope, drawer: value });
    const url = new URL(window.location.href);
    if (url.searchParams.get("page") !== "channels" || url.searchParams.get("project") !== projectId
      || url.searchParams.get("channel") !== channelId) return;
    if (value === "prd") {
      url.searchParams.set("preview", "prd");
      url.searchParams.set("thread", threadId);
    } else { url.searchParams.delete("preview"); url.searchParams.delete("reader"); }
    window.history.replaceState(window.history.state, "", url);
  };
  return [drawer, setDrawer] as const;
}
