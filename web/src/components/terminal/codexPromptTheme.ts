// Herdr 0.9.5 sends explicit RGB cells and its NDJSON bridge has no host-theme
// message. Identify Codex history blocks in the intact xterm buffer so a theme
// switch or a reattach also repaints already-submitted prompts.
import type { IBufferLine, Terminal } from "@xterm/xterm";

function hasPromptSurface(line: IBufferLine | undefined): boolean {
  const cell = line?.getCell(0);
  return !!cell?.isBgRGB() && cell.getBgColor() === 0x444444;
}

function hasPromptMarker(line: IBufferLine | undefined): boolean {
  const cell = line?.getCell(0);
  return cell?.getChars() === "›" && !!cell.isBold() && !!cell.isDim()
    && cell.isFgRGB() && cell.getFgColor() === 0xeeeeee;
}

export function trackCodexPromptRows(terminal: Terminal): () => void {
  const rows = terminal.element?.querySelector(".xterm-rows")?.children;
  if (!rows) return () => {};

  const paint = () => {
    const buffer = terminal.buffer.active;
    const top = buffer.viewportY;
    const end = top + rows.length;
    let y = top;
    while (y < end) {
      if (!hasPromptSurface(buffer.getLine(y))) {
        rows[y - top].classList.toggle("web-terminal-codex-prompt", false);
        y++;
        continue;
      }
      const start = y;
      let prompt = false;
      while (y < end && hasPromptSurface(buffer.getLine(y))) {
        prompt ||= hasPromptMarker(buffer.getLine(y));
        y++;
      }
      // A wrapped prompt may start above the viewport, or its top padding
      // may be the last visible row with the marker just below it.
      if (!prompt && start === top) {
        for (let i = top - 1; i >= 0 && !prompt && hasPromptSurface(buffer.getLine(i)); i--) {
          prompt = hasPromptMarker(buffer.getLine(i));
        }
      }
      if (!prompt && y === end && hasPromptSurface(buffer.getLine(end))) {
        prompt = hasPromptMarker(buffer.getLine(end));
      }
      for (let i = start; i < y; i++) {
        rows[i - top].classList.toggle("web-terminal-codex-prompt", prompt);
      }
    }
  };
  paint();
  const listener = terminal.onRender(paint);
  return () => {
    listener.dispose();
    for (const row of Array.from(rows)) row.classList.remove("web-terminal-codex-prompt");
  };
}
