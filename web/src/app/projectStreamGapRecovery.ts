import { useCallback, useEffect, useRef } from "react";
import {
  getChannelConversation,
  getChannels,
  getDeliveryFeatures,
  getKanbanPendingProposals,
  getRecentEventsPage,
  invalidateProjectReadCache,
  type PendingKanbanProposal,
} from "../api/client";
import type {
  ChannelDetail,
  ChannelsPage,
  DeliveryFeaturesPage,
  RecentEvent,
  Snapshot,
} from "../api/types";
import { channelIdOf } from "./shared";
import { LatestRequestGate } from "./latestRequestGate";
import type { PageId } from "./sharedTypes";
import { pageLoadsDeliveryFeatures, snapshotLoadKindForPage } from "./pageLoadPolicy";
import { loadPageSnapshot } from "./pageSnapshot";

interface ProjectStreamGapRecoveryOptions {
  activeProjectId: string;
  page: PageId;
  selectedChannelId: string;
  lastSeqRef: { current: number };
  setEvents: (value: RecentEvent[]) => void;
  setSnapshot: (value: Snapshot) => void;
  setDeliveryFeaturesPage: (value: DeliveryFeaturesPage) => void;
  setChannelsPage: (value: ChannelsPage) => void;
  setChannelLoadError: (value: string | null) => void;
  setSelectedChannelId: (value: string) => void;
  setChannelDetail: (value: ChannelDetail) => void;
  channelDetailRequestGate: LatestRequestGate;
  setKanbanPendingProposals: (value: PendingKanbanProposal[]) => void;
  setError: (value: string | null) => void;
}

export function useProjectStreamGapRecovery(
  options: ProjectStreamGapRecoveryOptions,
): (projectId: string) => Promise<number> {
  const optionsRef = useRef(options);
  const mountedRef = useRef(true);
  optionsRef.current = options;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  return useCallback(async (projectId: string) => {
    const initial = optionsRef.current;
    const recoveryPage = initial.page;
    assertCurrentRecovery(mountedRef.current, initial.activeProjectId, projectId, recoveryPage, initial.page);

    // Catch up the projection before choosing the replacement SSE cursor.
    // Events appended after this read remain above current_seq and are replayed
    // by the replacement stream instead of being skipped by a newer health seq.
    invalidateProjectReadCache(projectId);
    const nextEventsPage = await getRecentEventsPage(60, projectId, true);
    const authoritativeCursor = Number(nextEventsPage.current_seq);
    if (!Number.isInteger(authoritativeCursor) || authoritativeCursor < 0) {
      throw new Error("stream recovery returned an invalid event cursor");
    }

    const snapshotKind = snapshotLoadKindForPage(recoveryPage);
    const [nextSnapshot, nextDelivery, nextChannels, nextProposals] = await Promise.all([
      loadPageSnapshot(snapshotKind, projectId),
      pageLoadsDeliveryFeatures(recoveryPage)
        ? getDeliveryFeatures(projectId)
        : Promise.resolve<DeliveryFeaturesPage | undefined>(undefined),
      recoveryPage === "channels"
        ? getChannels(projectId)
        : Promise.resolve<ChannelsPage | undefined>(undefined),
      recoveryPage === "triage"
        ? getKanbanPendingProposals(projectId)
        : Promise.resolve<{ items: PendingKanbanProposal[] } | undefined>(undefined),
    ]);
    let current = optionsRef.current;
    assertCurrentRecovery(mountedRef.current, current.activeProjectId, projectId, recoveryPage, current.page);
    if (nextSnapshot?.project?.project_id && nextSnapshot.project.project_id !== projectId) {
      throw new Error("stream recovery snapshot belongs to another project");
    }

    let nextChannelDetail: ChannelDetail | undefined;
    let channelDetailTicket = 0;
    let recoveredChannelId = current.selectedChannelId;
    if (nextChannels) {
      if (!recoveredChannelId || !nextChannels.channels.some((item) => channelIdOf(item) === recoveredChannelId)) {
        recoveredChannelId = channelIdOf(nextChannels.channels[0]) || "ch-zaofu";
      }
      channelDetailTicket = current.channelDetailRequestGate.issue();
      nextChannelDetail = await getChannelConversation(
        recoveredChannelId,
        projectId,
        { requireFresh: true },
      );
    }

    current = optionsRef.current;
    assertCurrentRecovery(mountedRef.current, current.activeProjectId, projectId, recoveryPage, current.page);
    current.lastSeqRef.current = authoritativeCursor;
    current.setEvents(nextEventsPage.items.slice().reverse());
    if (nextSnapshot) current.setSnapshot(nextSnapshot);
    if (nextDelivery) current.setDeliveryFeaturesPage(nextDelivery);
    if (nextChannels) {
      current.setChannelsPage(nextChannels);
      current.setChannelLoadError(null);
      current.setSelectedChannelId(recoveredChannelId);
      if (nextChannelDetail && current.channelDetailRequestGate.isCurrent(channelDetailTicket)) {
        current.setChannelDetail(nextChannelDetail);
      }
    }
    if (nextProposals) current.setKanbanPendingProposals(nextProposals.items ?? []);
    current.setError(null);
    return authoritativeCursor;
  }, []);
}

function assertCurrentRecovery(
  mounted: boolean,
  activeProjectId: string,
  recoveryProjectId: string,
  recoveryPage: PageId,
  currentPage: PageId,
): void {
  if (!mounted || !recoveryProjectId || activeProjectId !== recoveryProjectId || currentPage !== recoveryPage) {
    throw new Error("stream recovery was superseded by project or page navigation");
  }
}
