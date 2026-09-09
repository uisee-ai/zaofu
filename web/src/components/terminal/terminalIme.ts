interface TerminalImeKeyboardEvent {
  isComposing: boolean;
  key: string;
  keyCode: number;
}

interface PendingImeCommit {
  data: string;
  observed: boolean;
  timer: number;
}

export interface TerminalImeInputGuard {
  dispose: () => void;
  observeTerminalData: (data: string) => void;
  shouldBypassXtermKeyEvent: (event: KeyboardEvent) => boolean;
}

/**
 * Let the browser IME own candidate-selection keys. xterm must not encode
 * Space, number, or paging keys while a composition is active.
 */
export function isTerminalImeKeyEvent(
  event: TerminalImeKeyboardEvent,
  compositionActive: boolean,
): boolean {
  // Process/keyCode 229 alone is not proof of an active composition. Some IMEs
  // use that legacy key shape before a plain insertText event, which xterm must
  // see so its textarea-diff recovery can emit the committed character.
  return compositionActive || event.isComposing;
}

/**
 * Work around xterm 6 composition commits that produce no onData event when
 * an IME replaces the hidden textarea value. Healthy xterm commits remain the
 * primary path; the compositionend payload is sent only when xterm emitted no
 * data for that commit during its deferred finalizer.
 */
export function attachTerminalImeInputGuard(
  textarea: HTMLTextAreaElement,
  sendMissingCommit: (data: string) => void,
): TerminalImeInputGuard {
  let compositionActive = false;
  const pending = new Set<PendingImeCommit>();

  const onCompositionStart = () => {
    compositionActive = true;
  };
  const onCompositionEnd = (event: CompositionEvent) => {
    compositionActive = false;
    if (!event.data) return;
    const commit: PendingImeCommit = {
      data: event.data,
      observed: false,
      timer: 0,
    };
    pending.add(commit);
    // xterm registers its compositionend listener first and schedules its own
    // zero-delay finalizer first. This callback therefore observes its onData
    // result before deciding whether the standards-based fallback is needed.
    commit.timer = window.setTimeout(() => {
      pending.delete(commit);
      if (!commit.observed) sendMissingCommit(commit.data);
    }, 0);
  };

  textarea.addEventListener("compositionstart", onCompositionStart);
  textarea.addEventListener("compositionend", onCompositionEnd);

  return {
    dispose: () => {
      textarea.removeEventListener("compositionstart", onCompositionStart);
      textarea.removeEventListener("compositionend", onCompositionEnd);
      for (const commit of pending) window.clearTimeout(commit.timer);
      pending.clear();
    },
    observeTerminalData: (data: string) => {
      if (!data) return;
      const exact = [...pending].find((commit) => data.includes(commit.data));
      if (exact) {
        exact.observed = true;
        return;
      }
      // A non-empty xterm emission may be an IME-specific transformed commit.
      // Treat it as delivered rather than risking a duplicate full fallback.
      if (pending.size === 1) {
        const [only] = pending;
        if (only) only.observed = true;
      }
    },
    shouldBypassXtermKeyEvent: (event: KeyboardEvent) => (
      isTerminalImeKeyEvent(event, compositionActive)
    ),
  };
}
