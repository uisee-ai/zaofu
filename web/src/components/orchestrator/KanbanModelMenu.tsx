import { Check, ChevronDown, ChevronRight, RotateCcw, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { useKanbanModelSelection } from "./useKanbanModelSelection";
import { kanbanEffortLabel } from "./kanbanModelSelection";

export function KanbanModelMenu({ selection, disabled, catalogSupported, unlocked, onSwitchToCodex, onReauthorize }: {
  selection: ReturnType<typeof useKanbanModelSelection>;
  disabled?: boolean;
  catalogSupported: boolean;
  unlocked: boolean;
  onSwitchToCodex?: () => void;
  onReauthorize?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"effort" | "models">("effort");
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  const modelName = selection.selected?.label || selection.defaultModel || "Default";
  const displayedEffort = selection.effort || selection.defaultEffort;
  const effortName = displayedEffort ? kanbanEffortLabel(displayedEffort) : "Auto";
  const levels = ["", ...selection.availableEfforts];
  const index = Math.max(0, levels.indexOf(displayedEffort));
  return (
    <div className="kanban-model-menu" ref={root}>
      <button ref={trigger} type="button" className="kanban-model-trigger" aria-label="Model and thinking effort"
        aria-haspopup="dialog" aria-expanded={open} disabled={disabled}
        onClick={() => { setView("effort"); setOpen((value) => !value); }}>
        <span className="kanban-model-name">{modelName}</span><span className="kanban-effort-label">{effortName}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open ? <div className="kanban-model-popover" role="dialog" aria-label="Model and thinking effort">
        {!catalogSupported ? <div className="kanban-model-empty">
          <span>Model selection is available with Codex.</span>
          {onSwitchToCodex ? <button type="button" onClick={onSwitchToCodex}>Switch to Codex</button> : null}
        </div> : !unlocked ? <p className="kanban-model-hint">Enable chat actions to choose a model and effort.</p>
          : selection.authorizationRejected ? <div className="kanban-model-empty"><span>Dashboard authorization was rejected. Unlock again to load Codex models.</span>
            {onReauthorize ? <button type="button" onClick={onReauthorize}>Re-enter action token</button> : null}</div>
          : selection.status === "unavailable" ? <div className="kanban-model-empty"><span>Model catalog unavailable.</span>
            <button type="button" onClick={selection.reload}>Retry</button></div>
          : selection.status === "loading" ? <p className="kanban-model-hint">Loading models…</p>
          : selection.selectionUnavailable ? <div className="kanban-model-empty"><span>The saved model or effort is no longer available.</span>
            <button type="button" onClick={selection.clear}>Reset selection</button></div>
          : view === "models" ? <div className="kanban-model-list" role="group" aria-label="Select model">
            <div className="kanban-model-list-title">Select model</div>
            <button type="button" aria-pressed={!selection.model} onClick={() => { selection.setModel(""); setView("effort"); }}>
              <span>Default <small>{selection.defaultModel ? `Use ${selection.defaultModel} session configuration` : "Use the session configuration"}</small></span>{!selection.model ? <Check size={16} /> : null}
            </button>
            {selection.catalog?.models.map((item) => <button type="button" key={item.model} aria-pressed={selection.model === item.model}
              onClick={() => { selection.setModel(item.model); setView("effort"); }}>
              <span>{item.label}</span>{selection.model === item.model ? <Check size={16} /> : null}
            </button>)}
          </div> : <>
            <div className="kanban-effort-title"><Zap size={16} className="kanban-effort-icon" aria-hidden="true" />{effortName}
              <button type="button" aria-label="Reset model and effort" title="Reset model and effort" onClick={selection.clear}><RotateCcw size={15} /></button>
            </div>
            <button type="button" className="kanban-model-current" aria-label="Choose model" onClick={() => setView("models")}>{modelName}<ChevronRight size={15} /></button>
            <div className="kanban-effort-track" style={{ background: `linear-gradient(to right, var(--brand) ${levels.length > 1 ? index / (levels.length - 1) * 100 : 0}%, color-mix(in oklab, var(--muted-foreground) 22%, var(--card)) ${levels.length > 1 ? index / (levels.length - 1) * 100 : 0}%)` }}>
              <div className="kanban-effort-dots" aria-hidden="true">{levels.map((value, i) => <span key={value || "auto"} className={i <= index ? "active" : ""} />)}</div>
              <input className="kanban-effort-slider" type="range" min="0" max={Math.max(0, levels.length - 1)} step="1" value={index}
                title={!selection.selected && !selection.defaultModel ? "Levels supported by every listed model" : "Thinking effort"}
                disabled={levels.length < 2} aria-label="Thinking effort" aria-valuetext={effortName}
                onChange={(event) => selection.setEffort(levels[Number(event.target.value)] || "")} />
            </div>
          </>}
      </div> : null}
    </div>
  );
}
