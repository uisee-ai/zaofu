import { useSettingsPreferences, getSettingsPreferences } from "../settings/useSettingsPreferences";
import type { CSSProperties, SyntheticEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { issueLabelFoldCount, labelExplanation } from "./issueTriageModel";
// Pale, readable semantic hues. The names and filtering remain upstream data.
const labelPalettes: Readonly<Record<string, readonly [string, string, string]>> = {
    bug: ['#fff0f1', '#efc1c7', '#a43a4a'],
    security: ['#fff0f1', '#efc1c7', '#a43a4a'],
    p0: ['#ffe6e7', '#eeb8bf', '#a3293b'],
    p1: ['#fff0f1', '#efc1c7', '#a43a4a'],
    p2: ['#fff1bd', '#e5cf80', '#715916'],
    p3: ['#eff4f8', '#d2dde7', '#52657b'],
    unknown: ['#f0eaff', '#d4c3ed', '#655092'],
    ui: ['#f0eaff', '#d4c3ed', '#655092'],
    ux: ['#f0eaff', '#d4c3ed', '#655092'],
    'web/ui': ['#f0eaff', '#d4c3ed', '#655092'],
    runtime: ['#eaf2ff', '#c0d3ee', '#366197'],
    'kernel/state': ['#eaf2ff', '#c0d3ee', '#366197'],
    performance: ['#e5f5f0', '#b8dfd2', '#256957'],
    documentation: ['#eaf5fa', '#bfd9e7', '#35677c'],
    enhancement: ['#eaf6e8', '#c4debe', '#456d39'],
    'test/regression': ['#fff1e4', '#e9d0b4', '#8a5a2c'],
};
/** One palette for list badges, detail badges, and label filter options. */
export function githubLabelStyle(name: string, colors?: Record<string, string>): CSSProperties {
    const key = name.toLowerCase();
    const palette = Object.prototype.hasOwnProperty.call(labelPalettes, key) ? labelPalettes[key] : undefined;
    if (palette) return { filter: getSettingsPreferences().labelPalette === "muted" ? "saturate(.55)" : undefined, backgroundColor: palette[0], borderColor: palette[1], color: palette[2] };
    const supplied = String(colors?.[name] ?? '').replace(/^#/, '');
    const accent = /^[0-9a-f]{6}$/i.test(supplied) ? supplied : '64748b';
    const rgb = [0, 2, 4].map(offset => parseInt(accent.slice(offset, offset + 2), 16));
    // Retain a custom label's hue without neon fills or nearly-white text.
    const mix = (weight: number) => `rgb(${rgb.map(value => Math.round(value * weight + 255 * (1 - weight))).join(', ')})`;
    const text = rgb.map(value => Math.round(value * .42));
    return { filter: getSettingsPreferences().labelPalette === "muted" ? "saturate(.55)" : undefined, backgroundColor: mix(.13), borderColor: mix(.30), color: `rgb(${text.join(', ')})` };
}
export function IssueLabelList({ labels, colors, selected = [], onSelect }: {
    labels: string[];
    colors?: Record<string, string>;
    selected?: string[] | null;
    onSelect: (label: string) => void;
}) {
    const {labelCount} = useSettingsPreferences();
    const folded = labels.length > labelCount;
    const [open, setOpen] = useState(false);
    const host = useRef<HTMLSpanElement>(null);
    useEffect(() => {
        if (!open)
            return;
        const close = (event: PointerEvent) => { if (!host.current?.contains(event.target as Node))
            setOpen(false); };
        const escape = (event: KeyboardEvent) => { if (event.key === 'Escape')
            setOpen(false); };
        document.addEventListener('pointerdown', close);
        document.addEventListener('keydown', escape);
        return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
    }, [open]);
    const stop = (event: SyntheticEvent) => event.stopPropagation();
    const badge = (label: string) => <button key={label} type="button" className={`label-badge ${selected === null || selected.includes(label) ? 'selected' : ''}`} style={githubLabelStyle(label, colors)} data-tooltip={labelExplanation(label)} aria-label={`Filter issues by label ${label}`} aria-pressed={selected === null || selected.includes(label)} onClick={event => { stop(event); onSelect(label); }} onKeyDown={stop}>{label}</button>;
    return <span className="labels" aria-label="GitHub labels">
    {(folded ? labels.slice(0, labelCount) : labels).map(badge)}
    {folded ? <span className="label-overflow" ref={host} onClick={stop} onKeyDown={stop}>
      <button className="label-badge" type="button" aria-expanded={open} aria-label={`Show ${labels.length - labelCount} more labels`} onClick={() => setOpen(value => !value)}>+{labels.length - labelCount}</button>
      {open ? <span className="label-options" role="group" aria-label="All labels">{labels.map(badge)}</span> : null}
    </span> : null}
  </span>;
}
