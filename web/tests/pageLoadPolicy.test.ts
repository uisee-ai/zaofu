import {
  pageLoadsDeliveryFeatures,
  pageLoadsSnapshot,
  pagePollsOperatorInbox,
  snapshotLoadKindForPage,
} from "../src/app/pageLoadPolicy.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function testChannelsUseSlimPath(): void {
  assert(snapshotLoadKindForPage("channels") === "none", "channels must not wait for project snapshot");
  assert(!pageLoadsSnapshot("channels"), "channels should load channel/read-event slices only");
  assert(!pageLoadsDeliveryFeatures("channels"), "channels should not bootstrap delivery features");
  assert(!pagePollsOperatorInbox("channels"), "channels should not poll operator inbox");
}

function testMeasureUsesDeliverySlice(): void {
  assert(snapshotLoadKindForPage("delivery") === "none", "delivery overview should not wait for snapshot");
  assert(snapshotLoadKindForPage("delivery-trace") === "none", "delivery trace should not wait for snapshot");
  assert(snapshotLoadKindForPage("goal-coverage") === "none", "goal coverage should not wait for snapshot");
  assert(snapshotLoadKindForPage("behavior-loop") === "none", "loop page should not wait for snapshot");
  assert(pageLoadsDeliveryFeatures("delivery"), "delivery overview should load delivery features");
  assert(pageLoadsDeliveryFeatures("goal-coverage"), "goal coverage should load delivery features");
  assert(pageLoadsDeliveryFeatures("behavior-loop"), "loop page should load delivery features");
}

function testSnapshotPagesStayExplicit(): void {
  assert(snapshotLoadKindForPage("board") === "light", "board should use light snapshot");
  assert(snapshotLoadKindForPage("task") === "light", "task detail shell should use light snapshot");
  assert(snapshotLoadKindForPage("traces") === "light", "trace compatibility route should keep the scoped light path");
  assert(snapshotLoadKindForPage("events") === "full", "events should use full observability snapshot");
  assert(snapshotLoadKindForPage("runs") === "full", "runs should use full observability snapshot");
}

function testInboxPollIsPageScoped(): void {
  assert(pagePollsOperatorInbox("inbox"), "inbox should poll operator inbox");
  assert(!pagePollsOperatorInbox("board"), "board should not poll operator inbox on initial load");
  assert(!pagePollsOperatorInbox("delivery"), "measure pages should not poll operator inbox on initial load");
}

testChannelsUseSlimPath();
testMeasureUsesDeliverySlice();
testSnapshotPagesStayExplicit();
testInboxPollIsPageScoped();
