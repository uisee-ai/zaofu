import { useCallback, useEffect, useRef, useState } from "react";
import { LatestRequestGate } from "../../app/latestRequestGate";
import {
  createTerminalSession,
  fetchTerminalSessions,
  renameTerminalSession,
  stopTerminalSession,
} from "./api";
import type { TerminalProvider, TerminalSessionsPage } from "./types";

export function useTerminalSessions(projectId: string) {
  const [page, setPage] = useState<TerminalSessionsPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const fetching = useRef(false);
  const gate = useRef(new LatestRequestGate());
  const refresh = useCallback(async (quiet = false) => {
    const ticket = gate.current.issue();
    if (!projectId) {
      setPage(null);
      setLoading(false);
      return;
    }
    fetching.current = true;
    if (!quiet) setLoading(true);
    try {
      const next = await fetchTerminalSessions(projectId);
      if (!gate.current.isCurrent(ticket)) return;
      setPage(next);
      setError("");
      return next;
    } catch (reason) {
      if (gate.current.isCurrent(ticket)) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (gate.current.isCurrent(ticket)) {
        fetching.current = false;
        setLoading(false);
      }
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
    const update = () => { if (!document.hidden && !fetching.current) void refresh(true); };
    const timer = window.setInterval(update, 15000);
    window.addEventListener("focus", update);
    return () => {
      gate.current.invalidate();
      window.clearInterval(timer);
      window.removeEventListener("focus", update);
    };
  }, [refresh]);

  const create = useCallback(async (provider: TerminalProvider, slot: string, title: string) => {
    setBusy(true);
    try {
      const response = await createTerminalSession(projectId, provider, slot, title);
      if (!response.session) throw new Error(response.reason ?? "terminal create returned no session");
      await refresh();
      return response.session;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh]);

  const rename = useCallback(async (sessionId: string, title: string) => {
    setBusy(true);
    try {
      const response = await renameTerminalSession(projectId, sessionId, title);
      if (!response.session) throw new Error(response.reason ?? "terminal rename returned no session");
      await refresh();
      return response.session;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh]);

  const stop = useCallback(async (sessionId: string) => {
    setBusy(true);
    try {
      await stopTerminalSession(projectId, sessionId);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh]);

  return { page, loading, busy, error, refresh, create, rename, stop };
}
