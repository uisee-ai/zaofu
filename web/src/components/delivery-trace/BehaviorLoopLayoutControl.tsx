import { LOOP_LAYOUT_MODES } from "./BehaviorLoopLayout";
import type { LoopLayoutMode, ResolvedLoopLayoutMode } from "./BehaviorLoopLayout";

export function BehaviorLoopLayoutControl({
  mode,
  onSelect,
  reason,
  resolvedMode,
}: {
  mode: LoopLayoutMode;
  onSelect: (mode: LoopLayoutMode) => void;
  reason: string;
  resolvedMode: ResolvedLoopLayoutMode;
}) {
  return (
    <section className="loop-layout-control" aria-label="Loop layout">
      <div>
        <strong>Layout</strong>
        <span>{mode === "auto" ? `Auto -> ${layoutLabel(resolvedMode)} (${reason})` : `${layoutLabel(resolvedMode)} (${reason})`}</span>
      </div>
      <div className="loop-layout-options" role="radiogroup" aria-label="Loop layout">
        {LOOP_LAYOUT_MODES.map((item) => (
          <button
            aria-checked={mode === item}
            className={`loop-layout-option ${mode === item ? "active" : ""}`}
            data-testid={`loop-layout-${item}`}
            key={item}
            role="radio"
            type="button"
            onClick={() => onSelect(item)}
          >
            {layoutLabel(item)}
          </button>
        ))}
      </div>
    </section>
  );
}

function layoutLabel(mode: LoopLayoutMode | ResolvedLoopLayoutMode): string {
  if (mode === "dag") return "DAG";
  return mode.charAt(0).toUpperCase() + mode.slice(1);
}
