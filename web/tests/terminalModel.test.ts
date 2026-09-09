import {
  acceptsTerminalFrame,
  activeTerminalAfterClose,
  closeTerminalTab,
  openTerminalTab,
  parseTerminalTabSnapshot,
  reconcileOpenTerminalTabs,
  serializeTerminalTabSnapshot,
  terminalModifierBits,
  terminalReconnectDelay,
  terminalScrollFromRows,
  terminalWheelDeltaRows,
} from "../src/components/terminal/terminalModel.js";
import type { TerminalSession } from "../src/components/terminal/types.js";
import { isTerminalImeKeyEvent } from "../src/components/terminal/terminalIme.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function session(id: string, state = "active"): TerminalSession {
  return {
    session_id: id,
    slot: id,
    title: id,
    provider: "codex",
    provider_kind: "codex",
    project_id: "project-a",
    state,
    generation: 1,
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T00:00:00Z",
    diagnostics: [],
  };
}

function testTabCloseOnlyDetaches(): void {
  const sessions = [session("a"), session("b")];
  const open = openTerminalTab(["a"], "b");
  const closed = closeTerminalTab(open, "a");

  assert(closed.length === 1 && closed[0] === "b", "closing a tab should remove only its attachment");
  assert(sessions[0].state === "active", "tab close must not mutate the terminal session lifecycle");
}

function testTabsReconcileAgainstProjectSessions(): void {
  const reconciled = reconcileOpenTerminalTabs(["a", "missing", "b"], [session("a"), session("b", "stopped")]);
  assert(reconciled.join(",") === "a,b", "known stopped session remains visible while unknown ids are removed");
}

function testActiveTabMovesToItsNearestNeighbor(): void {
  assert(activeTerminalAfterClose(["a", "b", "c"], "b", "b") === "c", "middle tab should select its right neighbor");
  assert(activeTerminalAfterClose(["a", "b"], "b", "b") === "a", "last tab should select its left neighbor");
  assert(activeTerminalAfterClose(["a", "b"], "a", "b") === "b", "closing an inactive tab must keep the active tab");
}

function testTabSnapshotIsVersionedAndRejectsInvalidStorage(): void {
  const serialized = serializeTerminalTabSnapshot({ activeId: "b", openIds: ["a", "b"] });
  const restored = parseTerminalTabSnapshot(serialized);

  assert(restored?.activeId === "b", "active terminal should survive a drawer remount");
  assert(restored?.openIds.join(",") === "a,b", "open terminal tabs should survive a drawer remount");
  assert(parseTerminalTabSnapshot('{"schema_version":"old","open_ids":["a"]}') === null, "stale tab storage must fail closed");
  assert(parseTerminalTabSnapshot("not-json") === null, "invalid tab storage must fail closed");
}

function testReconnectIsBounded(): void {
  assert(terminalReconnectDelay(0) === 250, "first retry should be prompt");
  assert(terminalReconnectDelay(99) === 5_000, "retry delay must stay bounded");
}

function testFramesRequireFullBaselineAndMonotonicSequence(): void {
  assert(!acceptsTerminalFrame(null, false, { seq: 1, full: false }), "delta cannot establish a baseline");
  assert(acceptsTerminalFrame(null, false, { seq: 1, full: true }), "full frame establishes baseline");
  assert(!acceptsTerminalFrame(2, true, { seq: 2, full: false }), "duplicate sequence must be rejected");
  assert(!acceptsTerminalFrame(2, true, { seq: 4, full: false }), "sequence gaps must be rejected");
  assert(acceptsTerminalFrame(2, true, { seq: 3, full: false }), "new delta should be accepted");
}

function testWheelDeltasNormalizeToTerminalRows(): void {
  assert(terminalWheelDeltaRows(-120, 0, 40) === -3, "120px wheel up should match Herdr's three-line default");
  assert(terminalWheelDeltaRows(3, 1, 40) === 3, "line-mode wheel deltas should remain terminal rows");
  assert(terminalWheelDeltaRows(-1, 2, 40) === -39, "page-mode wheel should use one visible page");
  assert(terminalWheelDeltaRows(Number.NaN, 0, 40) === 0, "invalid wheel deltas must be ignored");
}

function testAccumulatedRowsBecomeBoundedHerdrScrollCommands(): void {
  const up = terminalScrollFromRows(-2.6, "wheel");
  const down = terminalScrollFromRows(1_500, "page_key");

  assert(up?.direction === "up" && up.lines === 3 && up.source === "wheel", "negative rows should scroll up");
  assert(
    down?.direction === "down" && down.lines === 1_000 && down.source === "page_key",
    "scroll commands must stay within the Gateway limit",
  );
  assert(terminalScrollFromRows(0.49, "wheel") === null, "sub-row trackpad movement should remain buffered");
}

function testBrowserModifiersMapToCrosstermBits(): void {
  assert(
    terminalModifierBits({ shiftKey: true, ctrlKey: true, altKey: true, metaKey: true }) === 15,
    "browser modifiers should preserve Herdr/crossterm bit positions",
  );
}

function testImeOwnershipRequiresConfirmedComposition(): void {
  const key = (overrides: Partial<{
    isComposing: boolean;
    key: string;
    keyCode: number;
  }> = {}) => ({
    isComposing: false,
    key: " ",
    keyCode: 32,
    ...overrides,
  });

  assert(isTerminalImeKeyEvent(key(), true), "active composition should bypass xterm key encoding");
  assert(
    isTerminalImeKeyEvent(key({ isComposing: true, key: "1", keyCode: 49 }), false),
    "native composing candidate numbers should remain browser-owned",
  );
  assert(
    !isTerminalImeKeyEvent(key({ key: "Process", keyCode: 0 }), false),
    "Process outside a confirmed composition should retain xterm's textarea recovery",
  );
  assert(
    !isTerminalImeKeyEvent(key({ key: "Unidentified", keyCode: 229 }), false),
    "keyCode 229 outside a confirmed composition should retain xterm's textarea recovery",
  );
  assert(!isTerminalImeKeyEvent(key(), false), "ordinary spaces should still reach xterm");
}

testTabCloseOnlyDetaches();
testTabsReconcileAgainstProjectSessions();
testActiveTabMovesToItsNearestNeighbor();
testTabSnapshotIsVersionedAndRejectsInvalidStorage();
testReconnectIsBounded();
testFramesRequireFullBaselineAndMonotonicSequence();
testWheelDeltasNormalizeToTerminalRows();
testAccumulatedRowsBecomeBoundedHerdrScrollCommands();
testBrowserModifiersMapToCrosstermBits();
testImeOwnershipRequiresConfirmedComposition();
