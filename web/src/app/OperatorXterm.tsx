import "@xterm/xterm/css/xterm.css";

import { FitAddon } from "@xterm/addon-fit";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";
import { useEffect, useRef, useState } from "react";
import type { OperatorSession } from "../api/types";

interface OperatorXtermProps {
  canStart: boolean;
  enabled: boolean;
  onStart: (geometry: { cols: number; rows: number }) => void;
  onStop: () => Promise<void>;
  projectId?: string;
  session: OperatorSession | null;
  statusLabel: string;
}

type ControlMessage =
  | { type: "restore"; snapshot?: string; cols?: number | null; rows?: number | null }
  | { type: "state"; session?: OperatorSession }
  | { type: "exit"; code?: number | null }
  | { type: "error"; message?: string };

function wsUrl(path: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL(`${protocol}//${window.location.host}${path}`);
  const token = window.localStorage.getItem("zf.webActionToken")?.trim();
  if (token) {
    url.searchParams.set("token", token);
  }
  return url.toString();
}

function operatorWsPath(projectId: string | undefined, suffix: string): string {
  return projectId
    ? `/api/projects/${encodeURIComponent(projectId)}/operator/${suffix}`
    : `/api/operator/${suffix}`;
}

function arrayBufferToUint8Array(value: string | ArrayBuffer | Blob): string | Uint8Array | null {
  if (typeof value === "string") return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  return null;
}

function binaryStringToBytes(data: string): Uint8Array {
  const bytes = new Uint8Array(data.length);
  for (let index = 0; index < data.length; index += 1) {
    bytes[index] = data.charCodeAt(index) & 0xff;
  }
  return bytes;
}

export function OperatorXterm({
  canStart,
  enabled,
  onStart,
  onStop,
  projectId,
  session,
  statusLabel,
}: OperatorXtermProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const ioSocketRef = useRef<WebSocket | null>(null);
  const controlSocketRef = useRef<WebSocket | null>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const resizeTimerRef = useRef<number | null>(null);
  const [lastError, setLastError] = useState("");

  function currentGeometry() {
    const terminal = terminalRef.current;
    return {
      cols: terminal?.cols || 120,
      rows: terminal?.rows || 30,
    };
  }

  function sendResize() {
    const controlSocket = controlSocketRef.current;
    if (!controlSocket || controlSocket.readyState !== WebSocket.OPEN) return;
    fitRef.current?.fit();
    controlSocket.send(JSON.stringify({
      type: "resize",
      ...currentGeometry(),
    }));
  }

  useEffect(() => {
    if (!enabled || !containerRef.current) return undefined;

    const terminal = new Terminal({
      allowProposedApi: true,
      convertEol: false,
      cursorBlink: true,
      cursorStyle: "block",
      fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace',
      fontSize: 12,
      lineHeight: 1.15,
      scrollback: 5000,
      theme: {
        background: "#07090c",
        foreground: "#d9e4ec",
        cursor: "#62b7d8",
        selectionBackground: "#2b5363",
      },
    });
    const fit = new FitAddon();
    const unicode = new Unicode11Addon();
    terminal.loadAddon(fit);
    terminal.loadAddon(new WebLinksAddon());
    terminal.loadAddon(unicode);
    terminal.unicode.activeVersion = "11";
    terminal.open(containerRef.current);
    fit.fit();
    terminal.focus();
    terminalRef.current = terminal;
    fitRef.current = fit;

    const ioSocket = new WebSocket(wsUrl(operatorWsPath(projectId, "io")));
    ioSocket.binaryType = "arraybuffer";
    ioSocketRef.current = ioSocket;
    ioSocket.onopen = () => {
      setLastError("");
    };
    ioSocket.onmessage = (event) => {
      const data = arrayBufferToUint8Array(event.data);
      if (data !== null) terminal.write(data);
    };
    ioSocket.onerror = () => {
      setLastError("terminal IO websocket failed");
    };
    ioSocket.onclose = () => {
      if (ioSocketRef.current === ioSocket) {
        setLastError("terminal IO websocket closed");
      }
    };

    const controlSocket = new WebSocket(wsUrl(operatorWsPath(projectId, "control")));
    controlSocketRef.current = controlSocket;
    controlSocket.onopen = () => {
      setLastError("");
      sendResize();
    };
    controlSocket.onmessage = (event) => {
      let message: ControlMessage;
      try {
        message = JSON.parse(String(event.data)) as ControlMessage;
      } catch {
        return;
      }
      if (message.type === "restore") {
        terminal.reset();
        if (message.cols && message.rows) {
          terminal.resize(message.cols, message.rows);
        }
        if (message.snapshot) {
          terminal.write(message.snapshot);
        }
        controlSocket.send(JSON.stringify({ type: "restore_complete" }));
        window.requestAnimationFrame(sendResize);
        return;
      }
      if (message.type === "exit") {
        const label = message.code == null ? "session exited" : `session exited with code ${message.code}`;
        terminal.writeln(`\r\n[zaofu] ${label}`);
        return;
      }
      if (message.type === "error") {
        setLastError(message.message || "terminal control error");
      }
    };
    controlSocket.onerror = () => {
      setLastError("terminal control websocket failed");
    };
    controlSocket.onclose = () => {
      if (controlSocketRef.current === controlSocket) {
        setLastError("terminal control websocket closed");
      }
    };

    terminal.onData((data) => {
      if (ioSocket.readyState === WebSocket.OPEN) {
        ioSocket.send(data);
      }
    });
    terminal.onBinary((data) => {
      if (ioSocket.readyState === WebSocket.OPEN) {
        ioSocket.send(binaryStringToBytes(data));
      }
    });

    resizeObserverRef.current = new ResizeObserver(() => {
      if (resizeTimerRef.current !== null) {
        window.clearTimeout(resizeTimerRef.current);
      }
      resizeTimerRef.current = window.setTimeout(() => {
        resizeTimerRef.current = null;
        sendResize();
      }, 60);
    });
    resizeObserverRef.current.observe(containerRef.current);

    return () => {
      if (resizeTimerRef.current !== null) {
        window.clearTimeout(resizeTimerRef.current);
        resizeTimerRef.current = null;
      }
      resizeObserverRef.current?.disconnect();
      resizeObserverRef.current = null;
      ioSocketRef.current = null;
      controlSocketRef.current = null;
      ioSocket.close();
      controlSocket.close();
      terminal.dispose();
      terminalRef.current = null;
      fitRef.current = null;
    };
  }, [enabled, projectId]);

  return (
    <div className="xterm-shell">
      <div className="terminal-head">
        <span>Operator Terminal</span>
        <span className="mono">{statusLabel}</span>
      </div>
      <div className="xterm-host" ref={containerRef} aria-label="Operator xterm" />
      <div className="field-row">
        <button
          className="icon-button"
          disabled={!canStart}
          type="button"
          onClick={() => onStart(currentGeometry())}
        >
          Start
        </button>
        <button
          className="icon-button"
          disabled={!session?.alive}
          type="button"
          onClick={() => void onStop()}
        >
          Stop
        </button>
        <button
          className="icon-button"
          type="button"
          onClick={() => terminalRef.current?.clear()}
        >
          Clear
        </button>
        {lastError ? <span className="empty-text compact-error">{lastError}</span> : null}
      </div>
    </div>
  );
}
