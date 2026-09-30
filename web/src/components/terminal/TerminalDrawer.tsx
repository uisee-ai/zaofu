import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import {
  Check,
  CircleStop,
  Eye,
  Keyboard,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  SquareTerminal,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import type { ThemeMode } from "../../app/sharedTypes";
import { TerminalView } from "./TerminalView";
import {
  activeTerminalAfterClose,
  activeTerminalSessions,
  closeTerminalTab,
  openTerminalTab,
  parseTerminalTabSnapshot,
  reconcileOpenTerminalTabs,
  serializeTerminalTabSnapshot,
} from "./terminalModel";
import type {
  TerminalAttachmentMode,
  TerminalProvider,
  TerminalSession,
} from "./types";
import { useTerminalSessions } from "./useTerminalSessions";
import "./terminal.css";

interface TerminalDrawerProps {
  onClose: () => void;
  projectId: string;
  themeMode: ThemeMode;
}

interface TerminalTabsState {
  activeId: string;
  openIds: string[];
}

interface TerminalAttachmentState {
  mode: TerminalAttachmentMode;
  takeoverNonce: number;
}

interface TerminalConnectionState {
  error: string;
  status: string;
}

const PROVIDER_LABELS: Record<TerminalProvider, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  opencode: "OpenCode",
  pi: "Pi",
};
const DEFAULT_ATTACHMENT: TerminalAttachmentState = { mode: "control", takeoverNonce: 0 };
const DEFAULT_DOCK_HEIGHT = 420;
const MIN_DOCK_HEIGHT = 240;
const DOCK_HEIGHT_KEY = "zf.webTerminalDockHeight.v1";

function tabStorageKey(projectId: string): string {
  return `zf.webTerminalTabs.v1:${projectId}`;
}

function restoredTabState(projectId: string): TerminalTabsState | null {
  try {
    return parseTerminalTabSnapshot(window.sessionStorage.getItem(tabStorageKey(projectId)));
  } catch {
    return null;
  }
}

function maximumDockHeight(): number {
  return Math.max(MIN_DOCK_HEIGHT, window.innerHeight - 96);
}

function clampDockHeight(value: number): number {
  return Math.min(maximumDockHeight(), Math.max(MIN_DOCK_HEIGHT, Math.round(value)));
}

function restoredDockHeight(): number {
  try {
    const value = Number(window.localStorage.getItem(DOCK_HEIGHT_KEY));
    return Number.isFinite(value) && value > 0
      ? clampDockHeight(value)
      : clampDockHeight(DEFAULT_DOCK_HEIGHT);
  } catch {
    return DEFAULT_DOCK_HEIGHT;
  }
}

function connectionTone(session: TerminalSession, connection?: TerminalConnectionState): string {
  if (session.state !== "active") return "stopped";
  if (connection?.error || ["closed", "unavailable"].includes(connection?.status ?? "")) {
    return "error";
  }
  if (connection?.status.startsWith("reconnecting")) return "warning";
  if (["controlling", "observing"].includes(connection?.status ?? "")) return "ready";
  return "pending";
}

export function TerminalDrawer({ onClose, projectId, themeMode }: TerminalDrawerProps) {
  useSettingsPreferences();
  const { page, loading, busy, error, refresh, create, rename, stop } = useTerminalSessions(projectId);
  const activeSessions = useMemo(
    () => activeTerminalSessions(page?.sessions ?? []),
    [page?.sessions],
  );
  const restoredTabsRef = useRef<TerminalTabsState | null>(restoredTabState(projectId));
  const initializedTabsRef = useRef(false);
  const [tabs, setTabs] = useState<TerminalTabsState>(
    () => restoredTabsRef.current ?? { activeId: "", openIds: [] },
  );
  const [attachments, setAttachments] = useState<Record<string, TerminalAttachmentState>>({});
  const [connections, setConnections] = useState<Record<string, TerminalConnectionState>>({});
  const [fullscreen, setFullscreen] = useState(true);
  const [dockHeight, setDockHeight] = useState(restoredDockHeight);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [actionsMenuOpen, setActionsMenuOpen] = useState(false);
  const [renamingSessionId, setRenamingSessionId] = useState("");
  const [renameDraft, setRenameDraft] = useState("");
  const [stoppingSessionIds, setStoppingSessionIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );

  useEffect(() => {
    if (!page) return;
    const firstProjection = !initializedTabsRef.current;
    initializedTabsRef.current = true;
    setTabs((current) => {
      let openIds = reconcileOpenTerminalTabs(current.openIds, page.sessions);
      if (firstProjection && restoredTabsRef.current === null && openIds.length === 0) {
        openIds = activeSessions.map((session) => session.session_id);
      }
      const activeId = openIds.includes(current.activeId)
        ? current.activeId
        : (openIds[0] ?? "");
      if (activeId === current.activeId && openIds.join("\0") === current.openIds.join("\0")) {
        return current;
      }
      return { activeId, openIds };
    });
  }, [activeSessions, page]);

  useEffect(() => {
    if (!initializedTabsRef.current) return;
    try {
      window.sessionStorage.setItem(tabStorageKey(projectId), serializeTerminalTabSnapshot(tabs));
    } catch {
      // Session persistence is optional in private browser modes.
    }
  }, [projectId, tabs]);

  useEffect(() => {
    try {
      window.localStorage.setItem(DOCK_HEIGHT_KEY, String(dockHeight));
    } catch {
      // Dock sizing remains usable when browser storage is unavailable.
    }
  }, [dockHeight]);

  useEffect(() => {
    const resize = () => setDockHeight((height) => clampDockHeight(height));
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);

  useEffect(() => {
    if (!fullscreen && !addMenuOpen && !actionsMenuOpen && !renamingSessionId) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (renamingSessionId) {
        setRenamingSessionId("");
        setRenameDraft("");
      } else if (addMenuOpen || actionsMenuOpen) {
        setAddMenuOpen(false);
        setActionsMenuOpen(false);
      } else {
        setFullscreen(false);
      }
    };
    const handlePointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Element)) return;
      if (event.target.closest(".web-terminal-menu-anchor")) return;
      setAddMenuOpen(false);
      setActionsMenuOpen(false);
    };
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [actionsMenuOpen, addMenuOpen, fullscreen, renamingSessionId]);

  const openSessions = tabs.openIds
    .map((id) => page?.sessions.find((session) => session.session_id === id))
    .filter((session) => session !== undefined);
  const active = page?.sessions.find((session) => session.session_id === tabs.activeId);
  const unopenedSessions = activeSessions.filter(
    (session) => !tabs.openIds.includes(session.session_id),
  );
  const hasLaunchableProvider = (page?.allowed_providers.length ?? 0) > 0;
  const activeAttachment = active
    ? (attachments[active.session_id] ?? DEFAULT_ATTACHMENT)
    : DEFAULT_ATTACHMENT;
  const capabilityLabel = page?.capability.available
    ? `${page.capability.backend} ${page.capability.version}`.trim()
    : (page?.capability.reason || "Terminal runtime unavailable");

  const updateTerminalStatus = useCallback((
    sessionId: string,
    status: string,
    connectionError: string,
  ) => {
    setConnections((current) => {
      const previous = current[sessionId];
      if (previous?.status === status && previous.error === connectionError) return current;
      return { ...current, [sessionId]: { status, error: connectionError } };
    });
  }, []);

  function activateSession(sessionId: string) {
    setTabs((current) => ({ ...current, activeId: sessionId }));
    setActionsMenuOpen(false);
  }

  function openExistingSession(sessionId: string) {
    setTabs((current) => ({
      activeId: sessionId,
      openIds: openTerminalTab(current.openIds, sessionId),
    }));
    setAddMenuOpen(false);
  }

  async function createSession(provider: TerminalProvider) {
    const slot = `${provider.replace(/[^a-z0-9]/g, "-")}-${Date.now().toString(36)}`;
    const ordinal = activeSessions
      .filter((session) => session.provider === provider).length + 1;
    const title = `${PROVIDER_LABELS[provider]} ${ordinal}`;
    setAddMenuOpen(false);
    try {
      const session = await create(provider, slot, title);
      setTabs((current) => ({
        activeId: session.session_id,
        openIds: openTerminalTab(current.openIds, session.session_id),
      }));
      setAttachments((current) => ({
        ...current,
        [session.session_id]: DEFAULT_ATTACHMENT,
      }));
    } catch {
      // useTerminalSessions owns the user-facing error state.
    }
  }

  function beginRename(session: TerminalSession) {
    setRenamingSessionId(session.session_id);
    setRenameDraft(session.title);
    setActionsMenuOpen(false);
  }

  async function commitRename(session: TerminalSession) {
    const title = renameDraft.trim();
    setRenamingSessionId("");
    setRenameDraft("");
    if (!title || title === session.title) return;
    try {
      await rename(session.session_id, title);
    } catch {
      // useTerminalSessions owns the user-facing error state.
    }
  }

  async function stopActiveSession(sessionId: string) {
    setStoppingSessionIds((current) => new Set(current).add(sessionId));
    try {
      await stop(sessionId);
    } catch {
      // useTerminalSessions owns the user-facing action error.
    } finally {
      setStoppingSessionIds((current) => {
        const next = new Set(current);
        next.delete(sessionId);
        return next;
      });
    }
  }

  function closeTab(sessionId: string) {
    setTabs((current) => ({
      activeId: activeTerminalAfterClose(current.openIds, sessionId, current.activeId),
      openIds: closeTerminalTab(current.openIds, sessionId),
    }));
  }

  function setAttachmentMode(sessionId: string, mode: TerminalAttachmentMode) {
    setAttachments((current) => ({
      ...current,
      [sessionId]: { ...(current[sessionId] ?? DEFAULT_ATTACHMENT), mode },
    }));
    setActionsMenuOpen(false);
  }

  function takeOver(sessionId: string) {
    setAttachments((current) => {
      const previous = current[sessionId] ?? DEFAULT_ATTACHMENT;
      return {
        ...current,
        [sessionId]: {
          mode: "control",
          takeoverNonce: previous.takeoverNonce + 1,
        },
      };
    });
    setActionsMenuOpen(false);
  }

  function startResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (fullscreen || event.button !== 0) return;
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = dockHeight;
    const move = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      setDockHeight(clampDockHeight(startHeight + startY - moveEvent.clientY));
    };
    const finish = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
    };
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
  }

  function resizeWithKeyboard(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (fullscreen || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const delta = event.key === "ArrowUp" ? 24 : -24;
    setDockHeight((height) => clampDockHeight(height + delta));
  }

  const dockStyle = {
    "--web-terminal-dock-height": `${dockHeight}px`,
  } as CSSProperties;

  return (
    <div
      className={`web-terminal-layer ${fullscreen ? "is-fullscreen" : ""}`}
      style={dockStyle}
    >
      <aside
        className={`web-terminal-drawer ${fullscreen ? "is-fullscreen" : ""}`}
        aria-label="Coding Agent Terminal"
        data-fullscreen={fullscreen ? "true" : "false"}
      >
        <div
          className="web-terminal-resize-handle"
          role="separator"
          aria-label="Resize terminal dock"
          aria-orientation="horizontal"
          aria-valuemax={maximumDockHeight()}
          aria-valuemin={MIN_DOCK_HEIGHT}
          aria-valuenow={dockHeight}
          tabIndex={fullscreen ? -1 : 0}
          onKeyDown={resizeWithKeyboard}
          onPointerDown={startResize}
        >
          <span aria-hidden="true" />
        </div>

        <header className="web-terminal-toolbar">
          <span className="web-terminal-mark" title={capabilityLabel} aria-label={capabilityLabel}>
            <SquareTerminal aria-hidden="true" />
          </span>
          <div className="web-terminal-tabs" role="tablist" aria-label="Terminal sessions">
            {openSessions.map((session) => {
              const selected = tabs.activeId === session.session_id;
              const connection = connections[session.session_id];
              const tone = connectionTone(session, connection);
              return (
                <div
                  className={`web-terminal-tab-wrap ${selected ? "is-active" : ""} ${
                    renamingSessionId === session.session_id ? "is-renaming" : ""
                  }`.trim()}
                  key={session.session_id}
                >
                  {renamingSessionId === session.session_id ? (
                    <input
                      className="web-terminal-tab-rename"
                      id={`terminal-tab-${session.session_id}`}
                      aria-label={`Rename ${session.title}`}
                      autoFocus
                      maxLength={80}
                      value={renameDraft}
                      onBlur={() => {
                        setRenamingSessionId("");
                        setRenameDraft("");
                      }}
                      onChange={(event) => setRenameDraft(event.target.value)}
                      onKeyDown={(event) => {
                        event.stopPropagation();
                        if (event.key === "Enter") {
                          event.preventDefault();
                          void commitRename(session);
                        } else if (event.key === "Escape") {
                          setRenamingSessionId("");
                          setRenameDraft("");
                        }
                      }}
                    />
                  ) : (
                    <button
                      className="web-terminal-tab"
                      id={`terminal-tab-${session.session_id}`}
                      type="button"
                      role="tab"
                      aria-controls={`terminal-panel-${session.session_id}`}
                      aria-selected={selected}
                      data-session-id={session.session_id}
                      title={`${session.title} · ${PROVIDER_LABELS[session.provider]}`}
                      onClick={() => activateSession(session.session_id)}
                      onDoubleClick={() => beginRename(session)}
                    >
                      <span
                        className={`web-terminal-state-dot is-${tone}`}
                        title={connection?.error || connection?.status || session.state}
                        data-connection-status={connection?.status || session.state}
                        aria-hidden="true"
                      />
                      <span className="web-terminal-tab-label">{session.title}</span>
                    </button>
                  )}
                  <button
                    className="web-terminal-tab-close"
                    type="button"
                    aria-label={`Detach ${session.title}`}
                    title="Detach tab; the CLI keeps running"
                    onClick={() => closeTab(session.session_id)}
                  >
                    <X aria-hidden="true" />
                  </button>
                </div>
              );
            })}
            {openSessions.length === 0 ? (
              <span className="web-terminal-tabs-empty">{uiText("No terminal sessions")}</span>
            ) : null}
          </div>

          <div className="web-terminal-menu-anchor">
            <button
              className="web-terminal-toolbar-button"
              type="button"
              aria-label="New terminal"
              aria-expanded={addMenuOpen}
              disabled={!page?.enabled || !page.capability.available || !hasLaunchableProvider || busy}
              title={busy
                ? "Starting terminal…"
                : (hasLaunchableProvider
                  ? "New terminal"
                  : "This Project has no supported Coding Agent provider")}
              onClick={() => {
                setAddMenuOpen((value) => !value);
                setActionsMenuOpen(false);
              }}
            >
              <Plus aria-hidden="true" />
            </button>
            {addMenuOpen ? (
              <div className="web-terminal-menu web-terminal-add-menu" role="menu">
                <span className="web-terminal-menu-heading">{uiText("New session")}</span>
                {(page?.allowed_providers ?? []).map((value) => (
                  <button
                    key={value}
                    type="button"
                    role="menuitem"
                    onClick={() => void createSession(value)}
                  >
                    <Plus aria-hidden="true" /> {uiText("New")} {PROVIDER_LABELS[value]} terminal
                  </button>
                ))}
                {unopenedSessions.length > 0 ? (
                  <>
                    <span className="web-terminal-menu-heading has-divider">{uiText("Running sessions")}</span>
                    {unopenedSessions.map((session) => (
                      <button
                        key={session.session_id}
                        type="button"
                        role="menuitem"
                        onClick={() => openExistingSession(session.session_id)}
                      >
                        <SquareTerminal aria-hidden="true" />
                        {uiText("Open")} {session.title}
                      </button>
                    ))}
                  </>
                ) : null}
              </div>
            ) : null}
          </div>

          {active ? (
            <div className="web-terminal-menu-anchor">
              <button
                className="web-terminal-toolbar-button"
                type="button"
                aria-label="Terminal actions"
                aria-expanded={actionsMenuOpen}
                title="Terminal actions"
                onClick={() => {
                  setActionsMenuOpen((value) => !value);
                  setAddMenuOpen(false);
                }}
              >
                <MoreHorizontal aria-hidden="true" />
              </button>
              {actionsMenuOpen ? (
                <div className="web-terminal-menu web-terminal-actions-menu" role="menu">
                  <span className="web-terminal-menu-heading">
                    {active.title}
                  </span>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={busy || active.state !== "active" || !page?.capability.tab_rename}
                    title={page?.capability.tab_rename
                      ? "Rename Herdr tab"
                      : "Herdr tab rename is unavailable"}
                    onClick={() => beginRename(active)}
                  >
                    <Pencil aria-hidden="true" /> {uiText("Rename terminal")}
                  </button>
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={activeAttachment.mode === "observe"}
                    title="Read-only; multiple browsers can watch without taking input control"
                    onClick={() => setAttachmentMode(active.session_id, "observe")}
                  >
                    <Eye aria-hidden="true" /> Observe
                    {activeAttachment.mode === "observe" ? <Check aria-hidden="true" /> : null}
                  </button>
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={activeAttachment.mode === "control"}
                    title="Request normal input and resize control; one controller per session"
                    onClick={() => setAttachmentMode(active.session_id, "control")}
                  >
                    <Keyboard aria-hidden="true" /> Control
                    {activeAttachment.mode === "control" ? <Check aria-hidden="true" /> : null}
                  </button>
                  {page?.allow_takeover ? (
                    <button
                      type="button"
                      role="menuitem"
                      title="Explicitly replace the current controller on another browser or device"
                      onClick={() => takeOver(active.session_id)}
                    >
                      <Keyboard aria-hidden="true" /> {uiText("Take over control")}
                    </button>
                  ) : null}
                  <button
                    className="is-danger has-divider"
                    disabled={busy || active.state !== "active"}
                    type="button"
                    role="menuitem"
                    title="Explicitly stop the CLI; closing the dock only detaches"
                    onClick={() => {
                      setActionsMenuOpen(false);
                      void stopActiveSession(active.session_id);
                    }}
                  >
                    <CircleStop aria-hidden="true" /> {uiText("Stop CLI")}
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}

          <span className="web-terminal-toolbar-divider" aria-hidden="true" />
          <button
            className="web-terminal-toolbar-button"
            type="button"
            aria-label="Refresh terminal sessions"
            title="Refresh terminal sessions"
            onClick={() => void refresh()}
          >
            <RefreshCw aria-hidden="true" />
          </button>
          <button
            className="web-terminal-toolbar-button"
            type="button"
            aria-label={fullscreen ? "Exit full screen" : "Enter full screen"}
            title={fullscreen ? "Exit full screen (Esc)" : "Enter full screen"}
            aria-pressed={fullscreen}
            onClick={() => setFullscreen((value) => !value)}
          >
            {fullscreen ? <Minimize2 aria-hidden="true" /> : <Maximize2 aria-hidden="true" />}
          </button>
          <button
            className="web-terminal-toolbar-button"
            type="button"
            aria-label="Close terminal dock"
            title="Close dock; sessions keep running"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </button>
        </header>

        {loading ? <div className="web-terminal-notice">{uiText("Loading terminal runtime…")}</div> : null}
        {!loading && page && !page.enabled ? (
          <div className="web-terminal-notice is-error">
            {uiText("Web Terminal is disabled by this Dashboard host.")}
          </div>
        ) : null}
        {!loading && page?.enabled && !page.capability.available ? (
          <div className="web-terminal-notice is-error">{page.capability.reason}</div>
        ) : null}
        {!loading && page?.enabled && page.capability.available && !hasLaunchableProvider ? (
          <div className="web-terminal-notice">
            This Project has no supported Coding Agent provider in its effective configuration.
          </div>
        ) : null}
        {error ? <div className="web-terminal-notice is-error">{error}</div> : null}

        <div className="web-terminal-panels">
          {openSessions.map((session) => {
            const selected = tabs.activeId === session.session_id;
            const attachment = attachments[session.session_id] ?? DEFAULT_ATTACHMENT;
            return (
              <section
                className={`web-terminal-session-panel ${selected ? "is-active" : ""}`}
                id={`terminal-panel-${session.session_id}`}
                role="tabpanel"
                aria-hidden={!selected}
                aria-labelledby={`terminal-tab-${session.session_id}`}
                data-session-id={session.session_id}
                key={session.session_id}
              >
                <TerminalView
                  expectedClose={
                    session.state !== "active" || stoppingSessionIds.has(session.session_id)
                  }
                  mode={attachment.mode}
                  projectId={projectId}
                  session={session}
                  themeMode={themeMode}
                  takeoverNonce={attachment.takeoverNonce}
                  visible={selected}
                  onStatusChange={updateTerminalStatus}
                />
              </section>
            );
          })}
          {openSessions.length === 0 ? (
            <div className="web-terminal-empty">
              <SquareTerminal aria-hidden="true" />
              <p>{uiText("No terminal attached")}</p>
              <span>{uiText("Use + to start a coding agent or reopen a running session.")}</span>
            </div>
          ) : null}
        </div>
      </aside>
    </div>
  );
}

export default TerminalDrawer;
