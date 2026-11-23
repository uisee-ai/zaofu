import "@xterm/xterm/css/xterm.css";

import { FitAddon } from "@xterm/addon-fit";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";
import { useEffect, useRef, useState } from "react";
import type { ThemeMode } from "../../app/sharedTypes";
import { issueTerminalAttachment, terminalWebSocketUrl } from "./api";
import {
  acceptsTerminalFrame,
  terminalModifierBits,
  terminalReconnectDelay,
  terminalScrollFromRows,
  terminalWheelDeltaRows,
} from "./terminalModel";
import { trackCodexPromptRows } from "./codexPromptTheme";
import { attachTerminalImeInputGuard } from "./terminalIme";
import type {
  TerminalAttachmentMode,
  TerminalServerMessage,
  TerminalSession,
} from "./types";

interface TerminalViewProps {
  mouse: boolean;
  expectedClose: boolean;
  mode: TerminalAttachmentMode;
  onStatusChange: (sessionId: string, status: string, error: string) => void;
  projectId: string;
  session: TerminalSession;
  themeMode: ThemeMode;
  takeoverNonce: number;
  visible: boolean;
}

type ResolvedDashboardTheme = "dark" | "light";

const TERMINAL_THEMES = {
  dark: {
    background: "#202023",
    foreground: "#fafafa",
    cursor: "#7cb8ff",
    cursorAccent: "#202023",
    selectionBackground: "#35547d",
    selectionForeground: "#ffffff",
    selectionInactiveBackground: "#303b4c",
    black: "#27272a",
    red: "#f87171",
    green: "#4ade80",
    yellow: "#facc15",
    blue: "#60a5fa",
    magenta: "#c084fc",
    cyan: "#22d3ee",
    white: "#d4d4d8",
    brightBlack: "#71717a",
    brightRed: "#fca5a5",
    brightGreen: "#86efac",
    brightYellow: "#fde047",
    brightBlue: "#93c5fd",
    brightMagenta: "#d8b4fe",
    brightCyan: "#67e8f9",
    brightWhite: "#ffffff",
  },
  light: {
    background: "#ffffff",
    foreground: "#242428",
    cursor: "#2563eb",
    cursorAccent: "#ffffff",
    selectionBackground: "#cfe2ff",
    selectionForeground: "#172033",
    selectionInactiveBackground: "#e5edf8",
    black: "#27272a",
    red: "#b91c1c",
    green: "#15803d",
    yellow: "#a16207",
    blue: "#1d4ed8",
    magenta: "#7e22ce",
    cyan: "#0e7490",
    white: "#6b7280",
    brightBlack: "#52525b",
    brightRed: "#dc2626",
    brightGreen: "#16a34a",
    brightYellow: "#ca8a04",
    brightBlue: "#2563eb",
    brightMagenta: "#9333ea",
    brightCyan: "#0891b2",
    brightWhite: "#111827",
  },
} as const;

function resolveDashboardTheme(themeMode: ThemeMode): ResolvedDashboardTheme {
  if (themeMode === "dark") return "dark";
  if (themeMode === "light") return "light";
  return typeof window !== "undefined"
    && window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function useResolvedDashboardTheme(themeMode: ThemeMode): ResolvedDashboardTheme {
  const [resolved, setResolved] = useState<ResolvedDashboardTheme>(
    () => resolveDashboardTheme(themeMode),
  );

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setResolved(resolveDashboardTheme(themeMode));
    update();
    if (themeMode !== "system") return undefined;
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [themeMode]);

  return resolved;
}

function* terminalInputChunks(text: string): Generator<string> {
  const maxCodeUnits = 16_000;
  for (let offset = 0; offset < text.length;) {
    let end = Math.min(text.length, offset + maxCodeUnits);
    const trailing = text.charCodeAt(end - 1);
    const following = end < text.length ? text.charCodeAt(end) : 0;
    if (trailing >= 0xd800 && trailing <= 0xdbff && following >= 0xdc00 && following <= 0xdfff) {
      end -= 1;
    }
    yield text.slice(offset, end);
    offset = end;
  }
}

function terminalCellAtPointer(
  terminal: Terminal,
  host: HTMLElement,
  event: MouseEvent,
): { column: number; row: number } {
  const screen = terminal.element?.querySelector<HTMLElement>(".xterm-screen") ?? host;
  const bounds = screen.getBoundingClientRect();
  const width = Math.max(1, bounds.width);
  const height = Math.max(1, bounds.height);
  return {
    column: Math.max(0, Math.min(terminal.cols - 1, Math.floor(
      ((event.clientX - bounds.left) / width) * terminal.cols,
    ))),
    row: Math.max(0, Math.min(terminal.rows - 1, Math.floor(
      ((event.clientY - bounds.top) / height) * terminal.rows,
    ))),
  };
}

export function TerminalView({
  mouse,
  expectedClose,
  mode,
  onStatusChange,
  projectId,
  session,
  themeMode,
  takeoverNonce,
  visible,
}: TerminalViewProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const modeRef = useRef(mode);
  const mouseRef = useRef(mouse);
  const visibleRef = useRef(visible);
  const takeoverSeenRef = useRef(0);
  const expectedCloseRef = useRef(expectedClose);
  const retryTimerRef = useRef<number | null>(null);
  const retryAttemptRef = useRef(0);
  const [retryGeneration, setRetryGeneration] = useState(0);
  const [status, setStatus] = useState("connecting");
  const [error, setError] = useState("");
  const resolvedTheme = useResolvedDashboardTheme(themeMode);
  const terminalTheme = TERMINAL_THEMES[resolvedTheme];

  modeRef.current = mode;
  mouseRef.current = mouse;
  visibleRef.current = visible;
  expectedCloseRef.current = expectedClose;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const terminal = new Terminal({
      allowProposedApi: true,
      convertEol: false,
      cursorBlink: true,
      cursorStyle: "block",
      disableStdin: modeRef.current !== "control",
      fontFamily: '"Geist Mono", ui-monospace, Menlo, Consolas, monospace',
      fontSize: 13,
      lineHeight: 1.2,
      scrollback: 10_000,
      theme: terminalTheme,
    });
    const fit = new FitAddon();
    const unicode = new Unicode11Addon();
    terminal.loadAddon(fit);
    terminal.loadAddon(new WebLinksAddon());
    terminal.loadAddon(unicode);
    terminal.unicode.activeVersion = "11";
    terminal.open(host);
    if (visibleRef.current) fit.fit();
    terminalRef.current = terminal;
    fitRef.current = fit;

    const sendTerminalText = (text: string): void => {
      const socket = socketRef.current;
      if (modeRef.current !== "control" || socket?.readyState !== WebSocket.OPEN) return;
      for (const chunk of terminalInputChunks(text)) {
        if (socket.readyState !== WebSocket.OPEN) break;
        socket.send(JSON.stringify({ type: "terminal.input", text: chunk }));
      }
    };
    const textarea = terminal.textarea;
    if (!textarea) throw new Error("xterm input textarea is unavailable");
    const imeInput = attachTerminalImeInputGuard(textarea, sendTerminalText);

    const sendScroll = (
      rows: number,
      source: "wheel" | "page_key",
      metadata: Record<string, number> = {},
    ): boolean => {
      const socket = socketRef.current;
      const command = terminalScrollFromRows(rows, source);
      if (
        !command
        || modeRef.current !== "control"
        || socket?.readyState !== WebSocket.OPEN
      ) return false;
      socket.send(JSON.stringify({ ...command, ...metadata }));
      return true;
    };

    const screen = terminal.element!.querySelector<HTMLElement>(".xterm-screen")!;
    let press: { x: number; y: number; socket: WebSocket } | null = null;
    const startClick = (event: MouseEvent) => {
      const socket = socketRef.current;
      press = mouseRef.current && modeRef.current === "control" && event.button === 0
        && !event.shiftKey && !event.metaKey && socket?.readyState === WebSocket.OPEN
        && terminal.modes.mouseTrackingMode === "none"
        ? { x: event.clientX, y: event.clientY, socket } : null;
    };
    const finishClick = (event: MouseEvent) => {
      const start = press;
      press = null;
      if (!start || start.socket !== socketRef.current || start.socket.readyState !== WebSocket.OPEN
        || modeRef.current !== "control" || !mouseRef.current || event.button !== 0 || event.shiftKey || event.metaKey
        || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 3 || terminal.hasSelection()) return;
      const cell = terminalCellAtPointer(terminal, host, event);
      for (const action of ["down", "up"]) {
        start.socket.send(JSON.stringify({ type: "terminal.mouse", action, button: "left",
          ...cell, modifiers: terminalModifierBits(event) }));
      }
    };
    screen.addEventListener("mousedown", startClick);
    screen.addEventListener("click", finishClick);

    let pendingWheelRows = 0;
    let pendingWheelMetadata: Record<string, number> = {};
    let wheelAnimationFrame: number | null = null;
    terminal.attachCustomWheelEventHandler((event) => {
      const socket = socketRef.current;
      if (
        modeRef.current !== "control"
        || socket?.readyState !== WebSocket.OPEN
        || event.deltaY === 0
        || Math.abs(event.deltaX) > Math.abs(event.deltaY)
      ) return true;

      pendingWheelRows += terminalWheelDeltaRows(event.deltaY, event.deltaMode, terminal.rows);
      pendingWheelMetadata = {
        ...terminalCellAtPointer(terminal, host, event),
        modifiers: terminalModifierBits(event),
      };
      if (wheelAnimationFrame === null) {
        wheelAnimationFrame = window.requestAnimationFrame(() => {
          wheelAnimationFrame = null;
          if (terminalScrollFromRows(pendingWheelRows, "wheel")) {
            const rows = pendingWheelRows;
            pendingWheelRows = 0;
            sendScroll(rows, "wheel", pendingWheelMetadata);
          }
        });
      }
      event.preventDefault();
      return false;
    });
    terminal.attachCustomKeyEventHandler((event) => {
      if (imeInput.shouldBypassXtermKeyEvent(event)) return false;
      if (
        event.type !== "keydown"
        || event.altKey
        || event.ctrlKey
        || event.metaKey
        || event.shiftKey
        || (event.key !== "PageUp" && event.key !== "PageDown")
      ) return true;
      const rows = Math.max(1, terminal.rows - 1) * (event.key === "PageUp" ? -1 : 1);
      if (!sendScroll(rows, "page_key")) return true;
      event.preventDefault();
      return false;
    });

    const input = terminal.onData((text) => {
      imeInput.observeTerminalData(text);
      sendTerminalText(text);
    });
    const binary = terminal.onBinary((value) => {
      const socket = socketRef.current;
      if (modeRef.current === "control" && socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "terminal.input", bytes: window.btoa(value) }));
      }
    });
    let resizeTimer: number | null = null;
    const observer = new ResizeObserver(() => {
      if (!visibleRef.current) return;
      if (resizeTimer !== null) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        resizeTimer = null;
        fit.fit();
        const socket = socketRef.current;
        if (modeRef.current === "control" && socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({
            type: "terminal.resize",
            cols: terminal.cols,
            rows: terminal.rows,
          }));
        }
      }, 80);
    });
    observer.observe(host);
    return () => {
      if (wheelAnimationFrame !== null) window.cancelAnimationFrame(wheelAnimationFrame);
      if (resizeTimer !== null) window.clearTimeout(resizeTimer);
      screen.removeEventListener("mousedown", startClick);
      screen.removeEventListener("click", finishClick);
      observer.disconnect();
      imeInput.dispose();
      input.dispose();
      binary.dispose();
      terminal.dispose();
      terminalRef.current = null;
      fitRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (terminalRef.current) terminalRef.current.options.theme = terminalTheme;
  }, [terminalTheme]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (terminal && session.provider === "codex" && resolvedTheme === "light") {
      return trackCodexPromptRows(terminal);
    }
  }, [resolvedTheme, session.provider]);

  useEffect(() => {
    if (!visible) return undefined;
    const frame = window.requestAnimationFrame(() => {
      const terminal = terminalRef.current;
      fitRef.current?.fit();
      if (!terminal) return;
      const socket = socketRef.current;
      if (modeRef.current === "control" && socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
          type: "terminal.resize",
          cols: terminal.cols,
          rows: terminal.rows,
        }));
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [visible]);

  useEffect(() => {
    onStatusChange(session.session_id, status, error);
  }, [error, onStatusChange, session.session_id, status]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal || session.state !== "active") return undefined;
    terminal.options.disableStdin = mode !== "control";
    let cancelled = false;
    let socket: WebSocket | null = null;
    let hasFullFrame = false;
    let lastSeq: number | null = null;
    let pendingFrame: Extract<TerminalServerMessage, { type: "terminal.frame" }> | null = null;
    let streaming = false;

    const connect = async () => {
      setStatus("authorizing");
      setError("");
      fitRef.current?.fit();
      const takeover = mode === "control" && takeoverNonce > takeoverSeenRef.current;
      if (takeover) takeoverSeenRef.current = takeoverNonce;
      try {
        const ticket = await issueTerminalAttachment(
          projectId,
          session.session_id,
          mode,
          { cols: terminal.cols, rows: terminal.rows },
          takeover,
        );
        if (cancelled) return;
        socket = new WebSocket(
          terminalWebSocketUrl(projectId, session.session_id, mode),
          [ticket.subprotocol, ticket.ticket],
        );
        socket.binaryType = "arraybuffer";
        socketRef.current = socket;
        socket.onopen = () => {
          retryAttemptRef.current = 0;
          setStatus("waiting for full frame");
          if (mode === "control") {
            socket?.send(JSON.stringify({
              type: "terminal.resize",
              cols: terminal.cols,
              rows: terminal.rows,
            }));
          }
        };
        socket.onmessage = (event) => {
          if (event.data instanceof ArrayBuffer) {
            if (!pendingFrame) {
              setError("terminal bridge returned an unexpected binary payload");
              socket?.close(1002);
              return;
            }
            terminal.write(new Uint8Array(event.data));
            pendingFrame = null;
            if (!streaming) {
              streaming = true;
              setStatus(mode === "control" ? "controlling" : "observing");
            }
            return;
          }
          if (pendingFrame) {
            setError("terminal frame payload is missing; reconnecting");
            socket?.close(1002);
            return;
          }
          let message: TerminalServerMessage;
          try {
            message = JSON.parse(String(event.data)) as TerminalServerMessage;
          } catch {
            setError("terminal bridge returned invalid data");
            socket?.close(1002);
            return;
          }
          if (message.type === "terminal.closed") {
            setStatus("closed");
            if (expectedCloseRef.current) {
              setError("");
            } else if (message.reason) {
              setError(message.reason);
            }
            return;
          }
          if (!acceptsTerminalFrame(lastSeq, hasFullFrame, message)) {
            setError("terminal frame baseline/sequence is invalid; reconnecting");
            socket?.close(1002);
            return;
          }
          if (message.full) {
            terminal.reset();
            hasFullFrame = true;
          }
          lastSeq = message.seq;
          pendingFrame = message;
        };
        socket.onerror = () => setError("terminal websocket failed");
        socket.onclose = (event) => {
          if (socketRef.current === socket) socketRef.current = null;
          if (
            cancelled
            || session.state !== "active"
            || expectedCloseRef.current
            || event.code === 1000
            || event.code === 1008
            || event.code === 4401
            || event.code === 4403
          ) return;
          const delay = terminalReconnectDelay(retryAttemptRef.current);
          retryAttemptRef.current += 1;
          setStatus(`reconnecting in ${Math.round(delay / 100) / 10}s`);
          retryTimerRef.current = window.setTimeout(() => {
            retryTimerRef.current = null;
            setRetryGeneration((value) => value + 1);
          }, delay);
        };
      } catch (reason) {
        if (!cancelled) {
          setStatus("unavailable");
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      }
    };
    void connect();
    return () => {
      cancelled = true;
      if (retryTimerRef.current !== null) {
        window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      if (socketRef.current === socket) socketRef.current = null;
      socket?.close(1000, "attachment detached");
    };
  }, [mode, projectId, retryGeneration, session.session_id, session.state, takeoverNonce]);

  const visibleNotice = error || (status.startsWith("reconnecting") ? status : "");

  return (
    <div
      className="web-terminal-view"
      data-terminal-status={status}
      data-terminal-theme={resolvedTheme}
    >
      {visibleNotice ? (
        <div className="web-terminal-connection-notice" role="status">
          {visibleNotice}
        </div>
      ) : null}
      <div
        ref={hostRef}
        className="web-terminal-xterm-host"
        aria-label={`${session.title} terminal`}
        data-terminal-session-id={session.session_id}
        onMouseDown={() => terminalRef.current?.focus()}
      />
    </div>
  );
}
