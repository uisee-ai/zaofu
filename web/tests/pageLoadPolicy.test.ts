import {
  bootstrapEventCursor,
  channelEventRefreshPlan,
  eventInvalidatesLoopView,
  pageLoadsChannels,
  pageLoadsDeliveryFeatures,
  pageLoadsRecentEvents,
  pageLoadsSnapshot,
  pagePollsOperatorInbox,
  pageResourcePlanForPage,
  projectionNeedsFresh,
  snapshotLoadKindForPage,
} from "../src/app/pageLoadPolicy.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function testChannelsUseSlimPath(): void {
  assert(snapshotLoadKindForPage("channels") === "none", "channels must not wait for project snapshot");
  assert(!pageLoadsSnapshot("channels"), "channels should load channel/read-event slices only");
  assert(pageLoadsChannels("channels"), "channels should own its summary resource");
  assert(!pageLoadsRecentEvents("channels"), "channel history should come from its scoped detail resource");
  assert(!pageLoadsDeliveryFeatures("channels"), "channels should not bootstrap delivery features");
  assert(!pagePollsOperatorInbox("channels"), "channels should not poll operator inbox");
}

function testWorkflowProposalsUseScopedProjection(): void {
  assert(snapshotLoadKindForPage("workflows") === "none", "workflow proposals should not wait for snapshot");
  assert(!pageLoadsSnapshot("workflows"), "workflow proposals should load the request slice only");
  assert(!pageLoadsChannels("workflows"), "workflow proposals should not load channel summary");
  assert(!pageLoadsRecentEvents("workflows"), "workflow proposals should not load the global event window");
  assert(!pageLoadsDeliveryFeatures("workflows"), "workflow proposals should not load delivery features");
  assert(!pagePollsOperatorInbox("workflows"), "workflow proposals should not poll operator inbox");
}

function testMeasureUsesDeliverySlice(): void {
  assert(snapshotLoadKindForPage("delivery") === "none", "delivery overview should not wait for snapshot");
  assert(snapshotLoadKindForPage("delivery-trace") === "none", "delivery trace should not wait for snapshot");
  assert(snapshotLoadKindForPage("delivery-graph") === "none", "delivery graph should not wait for snapshot");
  assert(snapshotLoadKindForPage("behavior-loop") === "none", "loop page should not wait for snapshot");
  assert(pageLoadsDeliveryFeatures("delivery"), "delivery overview should load delivery features");
  assert(pageLoadsDeliveryFeatures("delivery-graph"), "delivery graph should load delivery features");
  assert(!pageLoadsDeliveryFeatures("behavior-loop"), "loop page should own only its scoped loop-view request");
  const loopPlan = pageResourcePlanForPage("behavior-loop").foreground;
  assert(loopPlan.length === 1 && loopPlan[0] === "project.health", "loop should not bootstrap Delivery resources");
}

function testLoopRefreshPolicyStaysScoped(): void {
  assert(eventInvalidatesLoopView("task.dispatched"), "task attempts should invalidate loop-view");
  assert(eventInvalidatesLoopView("verify.failed"), "verification backflow should invalidate loop-view");
  assert(eventInvalidatesLoopView("run.goal.completed"), "completion promise should invalidate loop-view");
  assert(eventInvalidatesLoopView("human.escalate"), "human-loop signals should invalidate loop-view");
  assert(eventInvalidatesLoopView("custom.profile.check.completed"), "custom terminal events must not be lost to a narrow allowlist");
  assert(!eventInvalidatesLoopView("worker.heartbeat"), "heartbeats must not cause Loop request storms");
  assert(!eventInvalidatesLoopView("task.attempt.heartbeat"), "derived attempt heartbeats must remain pump noise");
  assert(!eventInvalidatesLoopView("run.heartbeat"), "run heartbeats must remain pump noise");
  assert(!eventInvalidatesLoopView("run.manager.tick.completed"), "mechanical pump ticks should remain collapsed");
  assert(!eventInvalidatesLoopView("channel.message.stream.delta"), "unrelated stream deltas should not reload Loop");
}

function testSnapshotPagesStayExplicit(): void {
  assert(snapshotLoadKindForPage("board") === "board", "board should use its body-free snapshot");
  assert(snapshotLoadKindForPage("project") === "board", "overview needs the same task and cost fields, without report bodies");
  assert(snapshotLoadKindForPage("task") === "light", "task detail shell should use light snapshot");
  assert(snapshotLoadKindForPage("traces") === "none", "traces must load only its scoped index and detail resources");
  assert(snapshotLoadKindForPage("events") === "full", "events should use full observability snapshot");
  assert(snapshotLoadKindForPage("runs") === "full", "runs should use full observability snapshot");
}

function testInboxPollIsPageScoped(): void {
  assert(pagePollsOperatorInbox("inbox"), "inbox should poll operator inbox");
  assert(!pagePollsOperatorInbox("board"), "board should not poll operator inbox on initial load");
  assert(!pagePollsOperatorInbox("delivery"), "measure pages should not poll operator inbox on initial load");
}

function testBootstrapManifestIsPageOwned(): void {
  const channelPlan = pageResourcePlanForPage("channels").foreground;
  assert(channelPlan.includes("project.health"), "all project pages need the SSE cursor health resource");
  assert(channelPlan.includes("channels.summary"), "channel plan should include channel summary");
  assert(!channelPlan.includes("events.recent"), "channel plan should not duplicate scoped history");

  const boardPlan = pageResourcePlanForPage("board").foreground;
  assert(boardPlan.includes("project.snapshot.board"), "board should own the body-free snapshot");
  assert(!boardPlan.includes("project.snapshot.light"), "board should not duplicate the full light transport");
  assert(!boardPlan.includes("channels.summary"), "board should not bootstrap channels");
  assert(!boardPlan.includes("events.recent"), "board should not bootstrap recent events");

  const triagePlan = pageResourcePlanForPage("triage").foreground;
  assert(triagePlan.includes("events.recent"), "triage needs the bounded failed-event window");

  const inboxPlan = pageResourcePlanForPage("inbox").foreground;
  assert(inboxPlan.includes("operator.inbox"), "inbox should own its projection");
  assert(!inboxPlan.includes("channels.summary"), "inbox should not bootstrap channels");

  const tracePlan = pageResourcePlanForPage("traces").foreground;
  assert(tracePlan.length === 1, "traces should have one shared bootstrap dependency");
  assert(tracePlan[0] === "project.health", "traces should bootstrap only project health for the SSE cursor");
}

function testRecentWindowOwnsItsSseCursor(): void {
  assert(
    bootstrapEventCursor({ recentEventsSeq: 8, healthSeq: 10, snapshotSeq: 9 }) === 8,
    "a stale recent window must replay its tail through SSE instead of skipping to health",
  );
  assert(
    bootstrapEventCursor({ healthSeq: 10, snapshotSeq: 9, fallbackSeq: 7 }) === 10,
    "pages without event bodies should connect from the latest cheap cursor",
  );
}

function testProjectionFreshnessIncludesLayoutStale(): void {
  assert(projectionNeedsFresh({ projection_state: "ready", tail_behind: true }), "tail lag needs a fresh follow-up");
  assert(projectionNeedsFresh({ projection_state: "stale", tail_behind: false }), "rotation stale needs a fresh follow-up");
  assert(!projectionNeedsFresh({ projection_state: "ready", tail_behind: false }), "ready projection should not refetch");
}

function testChannelEventRefreshPlanSeparatesSummaryFromConversation(): void {
  assert(
    channelEventRefreshPlan("channel.agent.reply.completed").summary,
    "terminal replies change Channel attention summary",
  );
  assert(
    channelEventRefreshPlan("channel.message.posted").summary,
    "posted messages change Channel summary counts",
  );
  assert(
    !channelEventRefreshPlan("channel.message.stream.delta").summary,
    "stream deltas must not rebuild the Channel list",
  );
  assert(
    channelEventRefreshPlan("channel.message.stream.delta").conversation,
    "stream deltas still refresh the selected conversation",
  );
  assert(
    !channelEventRefreshPlan("task.updated").conversation,
    "non-Channel events do not refresh Channel slices",
  );
}

testChannelsUseSlimPath();
testWorkflowProposalsUseScopedProjection();
testMeasureUsesDeliverySlice();
testLoopRefreshPolicyStaysScoped();
testSnapshotPagesStayExplicit();
testInboxPollIsPageScoped();
testBootstrapManifestIsPageOwned();
testRecentWindowOwnsItsSseCursor();
testProjectionFreshnessIncludesLayoutStale();
testChannelEventRefreshPlanSeparatesSummaryFromConversation();
