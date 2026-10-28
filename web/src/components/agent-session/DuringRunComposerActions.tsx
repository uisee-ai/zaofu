import { ArrowUp, Clock3, Square, Zap, ZapOff } from "lucide-react";
import { useState } from "react";

interface DuringRunComposerActionsProps {
  canCancel: boolean;
  canInterrupt: boolean;
  canSteer: boolean;
  disabled?: boolean;
  sendingDisabled?: boolean;
  waitingForInput?: boolean;
  hasMessage: boolean;
  primaryAction: "queue" | "steer";
  onCancel: () => void;
  onQueue: () => void;
  onSteer: () => void;
  onSteerSooner: () => void;
}

export function DuringRunComposerActions({
  canCancel,
  canInterrupt,
  canSteer,
  disabled = false,
  sendingDisabled = false,
  waitingForInput = false,
  hasMessage,
  primaryAction,
  onCancel,
  onQueue,
  onSteer,
  onSteerSooner,
}: DuringRunComposerActionsProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  if (!hasMessage) {
    return (
      <button
        aria-label={waitingForInput ? "Cancel pending response" : "Stop generating"}
        className="headless-send-button"
        data-testid="during-run-stop-button"
        disabled={disabled || !canCancel}
        title={waitingForInput ? "Waiting for your answer · cancel response" : "Stop generating"}
        type="button"
        onClick={onCancel}
      >
        {waitingForInput ? <Clock3 size={21} /> : <Square className="composer-submit-stop" size={21} />}
      </button>
    );
  }

  const primaryIsSteer = primaryAction === "steer" && canSteer;
  const primaryLabel = primaryIsSteer ? "Steer this response" : "Queue message";
  const runPrimary = primaryIsSteer ? onSteer : onQueue;

  function runAction(action: () => void) {
    setMenuOpen(false);
    action();
  }

  return (
    <div className="during-run-composer-actions" data-primary-action={primaryIsSteer ? "steer" : "queue"}>
      <button
        aria-label="Steer this response sooner"
        className="during-run-steer-sooner"
        data-testid="during-run-steer-sooner"
        disabled={disabled || sendingDisabled || !canInterrupt}
        title="Tries to steer at the next safe point"
        type="button"
        onClick={() => runAction(onSteerSooner)}
      >
        <ZapOff size={16} />
      </button>
      <div
        className={`during-run-send-actions${menuOpen ? " open" : ""}`}
        onBlurCapture={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setMenuOpen(false);
        }}
        onFocusCapture={() => setMenuOpen(true)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setMenuOpen(false);
            (event.currentTarget.querySelector(".headless-send-button") as HTMLButtonElement | null)?.focus();
          }
        }}
        onMouseEnter={() => setMenuOpen(true)}
        onMouseLeave={() => setMenuOpen(false)}
      >
        <button
          aria-expanded={menuOpen}
          aria-haspopup="menu"
          aria-label={primaryLabel}
          className="headless-send-button"
          data-testid="during-run-send-button"
          disabled={disabled || sendingDisabled}
          title={primaryLabel}
          type="button"
          onClick={() => runAction(runPrimary)}
        >
          <ArrowUp size={21} />
        </button>
        <div aria-label="During-run actions" className="during-run-action-menu" role="menu">
          <button disabled={sendingDisabled || !canSteer} role="menuitem" type="button" onClick={() => runAction(onSteer)}>
            <Zap className="during-run-action-steer" size={16} />
            <span>Steer</span>
            {primaryIsSteer ? <kbd>Enter</kbd> : null}
          </button>
          <button disabled={sendingDisabled} role="menuitem" type="button" onClick={() => runAction(onQueue)}>
            <Clock3 className="during-run-action-queue" size={16} />
            <span>Queue</span>
            {!primaryIsSteer ? <kbd>Enter</kbd> : null}
          </button>
          <button
            disabled={sendingDisabled || !canInterrupt}
            role="menuitem"
            type="button"
            onClick={() => runAction(onSteerSooner)}
          >
            <ZapOff className="during-run-action-sooner" size={16} />
            <span>Steer sooner</span>
          </button>
          <div className="during-run-action-divider" role="separator" />
          <button
            className="danger"
            disabled={!canCancel}
            role="menuitem"
            type="button"
            onClick={() => runAction(onCancel)}
          >
            <Square size={15} />
            <span>{waitingForInput ? "Cancel pending response" : "Stop generating"}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
