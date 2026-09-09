import { useEffect } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";

import { getChannelConversation } from "../api/client";
import type { ChannelDetail } from "../api/types";
import { mergeChannelConversationRefresh } from "./channelConversationState";
import {
  CHANNEL_LIVE_CONVERGENCE_INTERVAL_MS,
  channelNeedsLiveConvergence,
} from "./channelLiveRefreshPolicy";
import { LatestRequestGate } from "./latestRequestGate";
import { ProjectRequestScope } from "./projectRequestScope";
import type { PageId } from "./sharedTypes";

interface ChannelLiveConvergenceArgs {
  activeProjectId: string;
  channelDetail: ChannelDetail | null;
  channelDetailRequestGateRef: MutableRefObject<LatestRequestGate>;
  page: PageId;
  projectRequestScope: ProjectRequestScope;
  selectedChannelId: string;
  selectedChannelIdRef: MutableRefObject<string>;
  setChannelDetail: Dispatch<SetStateAction<ChannelDetail | null>>;
}

/**
 * Reconciles an active provider-backed Channel conversation when an SSE event
 * is missed. This never changes canonical state; it only refreshes its Web
 * projection until the discussion settles.
 */
export function useChannelLiveConvergence({
  activeProjectId,
  channelDetail,
  channelDetailRequestGateRef,
  page,
  projectRequestScope,
  selectedChannelId,
  selectedChannelIdRef,
  setChannelDetail,
}: ChannelLiveConvergenceArgs): void {
  const active = channelNeedsLiveConvergence(channelDetail);

  useEffect(() => {
    if (page !== "channels" || !selectedChannelId || !active) return undefined;

    const channelId = selectedChannelId;
    const projectId = activeProjectId || "";
    let cancelled = false;
    let refreshBusy = false;
    async function refreshChannelConversation() {
      if (refreshBusy) return;
      refreshBusy = true;
      const ticket = projectRequestScope.capture(projectId);
      const detailTicket = channelDetailRequestGateRef.current.issue();
      try {
        const detail = await getChannelConversation(
          channelId,
          projectId || undefined,
          { requireFresh: true },
        );
        if (cancelled || !projectRequestScope.isCurrent(ticket)) return;
        if (selectedChannelIdRef.current !== channelId) return;
        if (!channelDetailRequestGateRef.current.isCurrent(detailTicket)) return;
        setChannelDetail((current) => mergeChannelConversationRefresh(current, detail));
      } catch {
        // SSE is primary; this closes a dropped projection-event gap only.
      } finally {
        refreshBusy = false;
      }
    }

    void refreshChannelConversation();
    const timer = window.setInterval(
      () => void refreshChannelConversation(),
      CHANNEL_LIVE_CONVERGENCE_INTERVAL_MS,
    );
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    active,
    activeProjectId,
    channelDetailRequestGateRef,
    page,
    projectRequestScope,
    selectedChannelId,
    selectedChannelIdRef,
    setChannelDetail,
  ]);
}
