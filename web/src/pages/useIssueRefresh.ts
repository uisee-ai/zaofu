import { useSyncExternalStore } from "react";
import { refreshIssueTriage } from "../api/client";

// Requests belong to the project, and outlive the currently mounted route.
const requests = new Map<string, Promise<unknown>>();
const listeners = new Set<() => void>();
const publish = () => listeners.forEach(listener => listener());
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};

export function useIssueRefresh(projectId: string) {
  const pending = useSyncExternalStore(subscribe, () => requests.has(projectId));
  const run = () => {
    const existing = requests.get(projectId);
    if (existing) return existing;
    const request = refreshIssueTriage(projectId, true).finally(() => {
      requests.delete(projectId);
      publish();
    });
    requests.set(projectId, request);
    publish();
    return request;
  };
  return { pending, run };
}
