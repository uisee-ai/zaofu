import { useEffect } from "react";

import { getEventsPage } from "../api/client";
import type {
  EventsPage,
} from "../api/types";
import type { ProjectRequestScope } from "./projectRequestScope";
import { projectionNeedsFresh } from "./pageLoadPolicy";
import { isObservabilityPage, parseEventFilter } from "./shared";
import type { PageId } from "./sharedTypes";

interface ProjectObservabilityDataOptions {
  activeProjectId: string;
  eventFilter: string;
  onError: (message: string | null) => void;
  onEventsPage: (page: EventsPage) => void;
  page: PageId;
  scope: ProjectRequestScope;
  selectedTaskId: string | null;
  snapshotSeq?: number;
}

export function useProjectObservabilityData({
  activeProjectId,
  eventFilter,
  onError,
  onEventsPage,
  page,
  scope,
  selectedTaskId,
  snapshotSeq,
}: ProjectObservabilityDataOptions): void {
  useEffect(() => {
    if (!isObservabilityPage(page)) return;
    let cancelled = false;
    const ticket = scope.capture(activeProjectId);
    const params = new URLSearchParams({ limit: "120" });
    const parsedFilter = parseEventFilter(eventFilter);
    const taskScope = parsedFilter.task || selectedTaskId || "";
    if (taskScope) params.set("task_id", taskScope);
    if (parsedFilter.actor) params.set("actor", parsedFilter.actor);
    if (parsedFilter.type) params.set("type", parsedFilter.type);
    else if (parsedFilter.prefix) params.set("prefix", parsedFilter.prefix);
    else if (parsedFilter.unknown[0]) params.set("type", parsedFilter.unknown[0]);
    if (parsedFilter.failed) params.set("failed", "true");
    if (parsedFilter.blocked) params.set("blocked", "true");
    void getEventsPage(params, activeProjectId || undefined).then((next) => {
      if (cancelled || !scope.isCurrent(ticket)) return;
      onEventsPage(next);
      if (projectionNeedsFresh(next)) {
        void getEventsPage(params, activeProjectId || undefined, true).then((fresh) => {
          if (!cancelled && scope.isCurrent(ticket)) onEventsPage(fresh);
        }).catch(() => undefined);
      }
    }).catch((error) => {
      if (!cancelled && scope.isCurrent(ticket)) {
        onError(error instanceof Error ? error.message : String(error));
      }
    });
    return () => { cancelled = true; };
  }, [activeProjectId, eventFilter, onError, onEventsPage, page, scope, selectedTaskId, snapshotSeq]);

}
