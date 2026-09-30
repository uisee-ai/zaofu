import { uiText } from "../settings/uiText";
import { getSettingsPreferences, useSettingsPreferences } from "../settings/useSettingsPreferences";
import { useCallback, useEffect, useRef, useState } from "react";
import { IssueIcon, type IssueIconName } from "./IssueIcon";
import { IssuePortal } from "./IssueOverlay";
export type IssueNotificationTone = "success" | "info" | "warning" | "danger";
export interface IssueNotificationInput {
    title: string;
    message: string;
    tone?: IssueNotificationTone;
    persistent?: boolean;
    duration?: number;
    category?: "recovery";
}
export interface IssueNotification extends IssueNotificationInput {
    id: number;
    closing?: boolean;
}
export function useIssueNotifications() {
    const [items, setItems] = useState<IssueNotification[]>([]);
    const nextId = useRef(0);
    const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
    const notify = useCallback((input: IssueNotificationInput) => {
        const prefs = getSettingsPreferences();
        const item = { ...input, duration: input.duration ?? (input.tone === 'success' ? prefs.successSeconds : prefs.infoSeconds) * 1000, id: ++nextId.current, tone: input.tone ?? 'info', persistent: input.persistent ?? (input.tone === 'warning' || input.tone === 'danger') };
        setItems(current => [...current, item]);
        return item.id;
    }, []);
    const dismiss = useCallback((id: number) => {
        setItems(current => current.map(item => item.id === id ? { ...item, closing: true } : item));
        const timer = setTimeout(() => {
            setItems(current => current.filter(item => item.id !== id));
            timers.current.delete(timer);
        }, 240);
        timers.current.add(timer);
    }, []);
    useEffect(() => () => { timers.current.forEach(clearTimeout); }, []);
    return { items, notify, dismiss };
}
export function IssueNotificationStack({ items, onDismiss }: {
    items: IssueNotification[];
    onDismiss: (id: number) => void;
}) {
    const prefs = useSettingsPreferences();
    const [hovered, setHovered] = useState(false);
    const [focused, setFocused] = useState(false);
    const [hidden, setHidden] = useState(() => document.visibilityState === 'hidden');
    useEffect(() => {
        const change = () => setHidden(document.visibilityState === 'hidden');
        document.addEventListener('visibilitychange', change);
        return () => document.removeEventListener('visibilitychange', change);
    }, []);
    useEffect(() => { if (!items.length) {
        setHovered(false);
        setFocused(false);
    } }, [items.length]);
    const viewport = useRef<HTMLDivElement>(null);
    const previous = useRef(0);
    useEffect(() => {
        if (items.length > previous.current && !hovered && !focused)
            viewport.current?.scrollTo({ top: viewport.current.scrollHeight, behavior: prefs.motion === 'standard' ? 'smooth' : 'auto' });
        previous.current = items.length;
    }, [items.length, hovered, focused]);
    return <IssuePortal><section className="notification-region" aria-label="Issue activity notifications" hidden={!items.length} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} onFocus={() => setFocused(true)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node))
        setFocused(false); }}>
    <header className="notification-heading" hidden={items.length < 4}><strong>{items.length} notifications</strong><span>{uiText("Scroll to review")}</span></header>
    <div className="notification-viewport" ref={viewport}><ol className="notification-stack">{items.map(item => <NotificationCard key={item.id} item={item} paused={hovered || focused || hidden} onDismiss={onDismiss}/>)}</ol></div>
  </section></IssuePortal>;
}
function NotificationCard({ item, paused, onDismiss }: {
    item: IssueNotification;
    paused: boolean;
    onDismiss: (id: number) => void;
}) {
    const [entered, setEntered] = useState(false);
    const duration = item.duration ?? 5000;
    const remaining = useRef(duration);
    useEffect(() => { const frame = requestAnimationFrame(() => setEntered(true)); return () => cancelAnimationFrame(frame); }, []);
    useEffect(() => {
        if (item.persistent || paused || item.closing)
            return;
        const start = performance.now();
        const timer = window.setTimeout(() => onDismiss(item.id), remaining.current);
        return () => { clearTimeout(timer); remaining.current = Math.max(0, remaining.current - (performance.now() - start)); };
    }, [item.id, item.persistent, item.closing, paused, onDismiss]);
    const icon: IssueIconName = item.tone === 'success' ? 'check' : item.tone === 'warning' ? 'warn' : item.tone === 'danger' ? 'xcircle' : 'info';
    return <li className={`toast-slot ${entered && !item.closing ? 'show' : ''} ${item.closing ? 'leaving' : ''}`}><div className="toast-clip"><article className={`toast ${item.tone || 'info'} ${item.persistent ? 'persistent' : ''}`} role={item.tone === 'danger' ? 'alert' : 'status'} aria-atomic="true">
    <span className="toast-status"><IssueIcon name={icon} size="md"/></span><div className="toast-copy"><strong>{item.title}</strong><span className="toast-message">{item.message}</span>{item.persistent ? <span className="toast-meta"><IssueIcon name="pin"/>{uiText("Stays until you dismiss it")}</span> : null}</div>
    <button className="toast-close" type="button" aria-label={`Dismiss ${item.title}`} onClick={() => onDismiss(item.id)}><IssueIcon name="x"/></button>
    {!item.persistent ? <span className="toast-progress" aria-hidden="true" style={{ animation: `issue-notification-countdown ${duration}ms linear forwards`, animationPlayState: paused ? 'paused' : 'running' }}/> : null}
  </article></div></li>;
}
