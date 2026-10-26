import { getBoardSnapshot, getSnapshot, getSnapshotLight } from "../api/client";
import type { Snapshot } from "../api/types";
import type { SnapshotLoadKind } from "./pageLoadPolicy";

// Bootstrap, manual refresh and stream recovery must use the same projection.
export function loadPageSnapshot(kind: Exclude<SnapshotLoadKind, "none">, projectId?: string): Promise<Snapshot>;
export function loadPageSnapshot(kind: SnapshotLoadKind, projectId?: string): Promise<Snapshot | null>;
export function loadPageSnapshot(kind: SnapshotLoadKind, projectId?: string): Promise<Snapshot | null> {
  if (kind === "none") return Promise.resolve(null);
  if (kind === "full") return getSnapshot(projectId);
  if (kind === "board") return getBoardSnapshot(projectId);
  return getSnapshotLight(projectId);
}
