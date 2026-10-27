import { useEffect, useState } from "react";
import { getBoardCards, getBoardTasks, type BoardCards, type BoardTaskBootstrap } from "../api/client";
import type { Snapshot } from "../api/types";

// A separate type keeps incomplete task records out of metrics/actions snapshots.
export function useBoardBootstrap(projectId: string, enabled: boolean, snapshot: Snapshot | null) {
  const [value, setValue] = useState<BoardTaskBootstrap | null>(null);
  const [cards, setCards] = useState<BoardCards | null>(null);
  const complete = snapshot?.project.project_id === projectId;
  useEffect(() => {
    if (!projectId || !enabled || complete) return;
    const controller = new AbortController();
    let cancelled = false;
    let baseTimer: ReturnType<typeof setTimeout>;
    let cardsTimer: ReturnType<typeof setTimeout>;
    let cardsReceived = false;
    setValue(null);
    setCards(null);
    const refreshBase = async () => {
      try {
        const next = await getBoardTasks(projectId, controller.signal);
        if (!cancelled && next.project_id === projectId && next.source === "TaskStore"
          && Array.isArray(next.tasks) && Array.isArray(next.archive_tasks)) setValue(next);
      } catch { /* The full snapshot owns errors; never replace records with zeros. */ }
      if (!cancelled && !cardsReceived) baseTimer = setTimeout(() => { void refreshBase(); }, 5000);
    };
    const refreshCards = async () => {
      try {
        const next = await getBoardCards(projectId, controller.signal);
        if (!cancelled && next.project_id === projectId && next.source === "projection"
          && Array.isArray(next.tasks) && Array.isArray(next.archive_tasks)
          && next.read_projection && ["ready", "refreshing", "error"].includes(next.read_projection.state)) {
          cardsReceived = true;
          clearTimeout(baseTimer);
          setCards(next);
        }
      } catch {
        // Keep the last complete cards, but never describe them as current.
        if (!cancelled) setCards(current => current?.project_id === projectId
          ? { ...current, read_projection: { ...current.read_projection, state: "error" } }
          : current);
      }
      if (!cancelled) cardsTimer = setTimeout(() => { void refreshCards(); }, 5000);
    };
    void refreshBase();
    void refreshCards();
    return () => { cancelled = true; controller.abort(); clearTimeout(baseTimer); clearTimeout(cardsTimer); };
  }, [projectId, enabled, complete]);
  if (!enabled || complete) return null;
  return cards?.project_id === projectId ? cards : value?.project_id === projectId ? value : null;
}
