import { useEffect } from "react";
import type { Snapshot } from "../api/types";
import type { PageId } from "./sharedTypes";
import { snapshotLoadKindForPage } from "./pageLoadPolicy";
import { loadPageSnapshot } from "./pageSnapshot";

export function useDashboardProjectionRefresh(projectId: string, page: PageId, snapshot: Snapshot | null, onSnapshot: (next: Snapshot) => void) {
  useEffect(() => {
    if (!projectId || !snapshot?.read_projection) return;
    const kind = snapshotLoadKindForPage(page);
    if (kind !== "board" && kind !== "observability") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = () => {
      void loadPageSnapshot(kind, projectId).then(next => {
        if (!cancelled && next?.project.project_id === projectId) onSnapshot(next);
      }).catch(() => {
        if (!cancelled) timer = setTimeout(refresh, 5000);
      });
    };
    timer = setTimeout(refresh, snapshot.read_projection.state === "ready" ? 30_000 : 2000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [projectId, page, snapshot, onSnapshot]);
}
