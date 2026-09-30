import { useSettingsPreferences } from "../settings/useSettingsPreferences";
import type { CSSProperties, ReactNode } from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { issueTooltipDelayMs } from "./issueTriageModel";
/** All floating UI inherits the workspace theme, without changing the dashboard. */
export function IssuePortal({ children }: {
    children: ReactNode;
}) {
    const prefs = useSettingsPreferences();
    return typeof document === "undefined" ? null : createPortal(<div className="issue-workspace issue-overlay-scope" data-issue-motion={prefs.motion} style={{ "--issue-visible-rows": prefs.visibleRows } as CSSProperties}>{children}</div>, document.body);
}
const FOCUSABLE = 'button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]';
export function IssueDialog({ open, busy = false, className, labelledBy, describedBy, role = "dialog", initialFocus, onClose, children }: {
    open: boolean;
    busy?: boolean;
    className: string;
    labelledBy: string;
    describedBy?: string;
    role?: "dialog" | "alertdialog";
    initialFocus?: string;
    onClose: () => void;
    children: ReactNode;
}) {
    const [present, setPresent] = useState(open);
    const [visible, setVisible] = useState(false);
    const panel = useRef<HTMLDivElement>(null);
    const returnFocus = useRef<HTMLElement | null>(null);
    const latest = useRef({ busy, onClose });
    latest.current = { busy, onClose };
    useEffect(() => {
        let frame = 0;
        let secondFrame = 0;
        let timer = 0;
        if (open) {
            returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            setPresent(true);
            frame = requestAnimationFrame(() => {
                secondFrame = requestAnimationFrame(() => setVisible(true));
            });
        }
        else {
            setVisible(false);
            timer = window.setTimeout(() => {
                setPresent(false);
                if (returnFocus.current?.isConnected)
                    returnFocus.current.focus();
            }, 220);
        }
        return () => { cancelAnimationFrame(frame); cancelAnimationFrame(secondFrame); clearTimeout(timer); };
    }, [open, initialFocus]);
    useEffect(() => {
        if (!visible || !present)
            return;
        // A visibility:hidden dialog cannot accept focus. Wait until its entering
        // class is committed, then focus the safe action (or the manual input).
        const frame = requestAnimationFrame(() => {
            const target = initialFocus ? panel.current?.querySelector<HTMLElement>(initialFocus) : null;
            (target ?? panel.current?.querySelector<HTMLElement>(FOCUSABLE) ?? panel.current)?.focus();
        });
        return () => cancelAnimationFrame(frame);
    }, [visible, present, initialFocus]);
    useEffect(() => {
        if (!present)
            return;
        const key = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                if (!latest.current.busy)
                    latest.current.onClose();
            }
            if (event.key !== "Tab")
                return;
            const nodes = Array.from(panel.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])
                .filter(node => node.getClientRects().length && !node.closest('[inert],[aria-hidden="true"]'));
            if (!nodes.length) {
                event.preventDefault();
                panel.current?.focus();
                return;
            }
            const index = nodes.indexOf(document.activeElement as HTMLElement);
            if (index < 0 || (!event.shiftKey && index === nodes.length - 1) || (event.shiftKey && index === 0)) {
                event.preventDefault();
                nodes[event.shiftKey ? nodes.length - 1 : 0].focus();
            }
        };
        document.addEventListener("keydown", key, true);
        return () => document.removeEventListener("keydown", key, true);
    }, [present]);
    if (!present)
        return null;
    return <IssuePortal><div className={`modal-backdrop ${visible ? "open" : ""}`} onMouseDown={event => {
            if (event.target === event.currentTarget && !busy)
                onClose();
        }}><div className={`modal ${className}`} ref={panel} tabIndex={-1} role={role} aria-modal="true" aria-labelledby={labelledBy} aria-describedby={describedBy} aria-busy={busy}>{children}</div></div></IssuePortal>;
}
/** One delayed tooltip layer shared by cards, badges, buttons and portal contents. */
export function IssueTooltipLayer() {
    const prefs = useSettingsPreferences();
    const [tip, setTip] = useState<{
        text: string;
        target: HTMLElement;
        kind?: string;
        heading?: string;
        state?: string;
        summary?: string;
    } | null>(null);
    const [visible, setVisible] = useState(false);
    const [position, setPosition] = useState({ left: 12, top: 12, above: false, anchor: 0 });
    const element = useRef<HTMLDivElement>(null);
    const target = useRef<HTMLElement | null>(null);
    const timer = useRef<number>(0);
    useEffect(() => {
        const hide = () => { clearTimeout(timer.current); target.current = null; setVisible(false); };
        hide();
        const scheduleTarget = (next: HTMLElement | null) => {
            if (!prefs.tooltips || !next?.closest('.issue-workspace') || !next.dataset.tooltip?.trim()) {
                hide();
                return;
            }
            if (target.current === next)
                return;
            hide();
            target.current = next;
            timer.current = window.setTimeout(() => {
                if (target.current !== next || !next.isConnected)
                    return;
                if (next.dataset.tooltipKind === 'lifecycle' && !next.matches(':hover') && !next.contains(document.activeElement)) {
                    hide();
                    return;
                }
                setTip({ text: next.dataset.tooltip!.trim(), target: next, kind: next.dataset.tooltipKind, heading: next.dataset.tooltipHeading, state: next.dataset.tooltipState, summary: next.dataset.tooltipSummary });
                setVisible(true);
            }, (next.dataset.tooltipKind === "stage-state" || next.closest(".stage-menu") ? prefs.stageTooltipDelay : prefs.tooltipDelay));
        };
        const schedule = (event: Event) => {
            scheduleTarget(event.target instanceof Element ? event.target.closest<HTMLElement>('[data-tooltip]') : null);
        };
        const viewportChanged = () => {
            const previous = target.current;
            hide();
            // Scrolling a node into view must not swallow its pending hint.
            // Rearm only this rich hint; verify hover/focus after scrolling settles.
            if (previous?.dataset.tooltipKind === 'lifecycle' && previous.isConnected)
                scheduleTarget(previous);
        };
        const leave = (event: Event) => {
            const next = (event as MouseEvent | FocusEvent).relatedTarget;
            if (!(next instanceof Node) || !target.current?.contains(next))
                hide();
        };
        const escape = (event: KeyboardEvent) => { if (event.key === 'Escape')
            hide(); };
        document.addEventListener('pointerover', schedule);
        document.addEventListener('pointerout', leave);
        document.addEventListener('focusin', schedule);
        document.addEventListener('focusout', leave);
        document.addEventListener('pointerdown', hide, true);
        document.addEventListener('keydown', escape);
        window.addEventListener('scroll', viewportChanged, true);
        window.addEventListener('resize', viewportChanged);
        return () => {
            hide();
            document.removeEventListener('pointerover', schedule);
            document.removeEventListener('pointerout', leave);
            document.removeEventListener('focusin', schedule);
            document.removeEventListener('focusout', leave);
            document.removeEventListener('pointerdown', hide, true);
            document.removeEventListener('keydown', escape);
            window.removeEventListener('scroll', viewportChanged, true);
            window.removeEventListener('resize', viewportChanged);
        };
    }, [prefs.tooltips, prefs.tooltipDelay, prefs.stageTooltipDelay]);
    useLayoutEffect(() => {
        if (!visible || !tip || !element.current)
            return;
        const box = tip.target.getBoundingClientRect();
        const width = element.current.offsetWidth;
        const height = element.current.offsetHeight;
        // Lifecycle hints sit above the node like the reference. Use a portal
        // so detail scrolling and the 3D card cannot clip the white hint.
        const gap = tip.kind === 'lifecycle' ? 10 : 7;
        const fitsAbove = box.top >= height + gap + 12;
        const above = tip.kind === 'lifecycle' ? fitsAbove : box.bottom + height + 18 > window.innerHeight;
        const left = Math.max(12, Math.min(window.innerWidth - width - 12, box.left + (box.width - width) / 2));
        setPosition({ left, top: above ? Math.max(12 + height, box.top - gap) : box.bottom + gap, above,
            anchor: Math.max(12, Math.min(width - 12, box.left + box.width / 2 - left)) });
        const previous = tip.target.getAttribute('aria-describedby');
        tip.target.setAttribute('aria-describedby', [previous, 'issue-tooltip'].filter(Boolean).join(' '));
        return () => { if (previous)
            tip.target.setAttribute('aria-describedby', previous);
        else
            tip.target.removeAttribute('aria-describedby'); };
    }, [tip, visible]);
    return <IssuePortal><div ref={element} id="issue-tooltip" role="tooltip" aria-hidden={!visible} className={`issue-floating-tooltip ${tip?.kind === 'lifecycle' ? 'lifecycle-hint' : ''} ${visible ? "visible" : ""} ${position.above ? "above" : ""}`} style={{ left: position.left, top: position.top, '--tip-anchor': `${position.anchor}px` } as CSSProperties}>{tip?.kind === 'lifecycle' ? <><strong>{tip.heading}</strong><span>{tip.state}</span><em>{tip.summary}</em></> : tip?.text}</div></IssuePortal>;
}
