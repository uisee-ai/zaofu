import { useSettingsPreferences } from "../settings/useSettingsPreferences";
import { useEffect, useRef, useState } from "react";
import { IssueIcon } from "./IssueIcon";
import { IssuePortal } from "./IssueOverlay";
import { issueTooltipDelayMs } from "./issueTriageModel";
export function IssueIdentity({ login, avatarUrl }: {
    login: string;
    avatarUrl?: string;
}) {
    return <a className="identity" href={`https://github.com/${encodeURIComponent(login)}`} target="_blank" rel="noopener noreferrer">{avatarUrl?.startsWith('https://') ? <img className="avatar" src={avatarUrl} alt="" loading="lazy" referrerPolicy="no-referrer"/> : <span className="avatar">{login[0]?.toUpperCase() || '?'}</span>}{login}</a>;
}
export function IssueAuthor({ login, avatarUrl, stats }: {
    login: string;
    avatarUrl?: string;
    stats?: {
        open: number;
        closed: number;
    };
}) {
    const prefs = useSettingsPreferences();
    const host = useRef<HTMLSpanElement>(null);
    const [position, setPosition] = useState<{
        left: number;
        top: number;
    } | null>(null);
    const timer = useRef<ReturnType<typeof setTimeout>>();
    const clear = () => { clearTimeout(timer.current); timer.current = undefined; };
    const open = () => {
        clear();
        if (!prefs.tooltips) return;
        timer.current = setTimeout(() => {
            const rect = host.current?.getBoundingClientRect();
            if (!rect)
                return;
            setPosition({ left: Math.max(12, Math.min(window.innerWidth - 242, rect.left)), top: rect.top > 145 ? rect.top - 132 : rect.bottom + 7 });
        }, prefs.tooltipDelay);
    };
    useEffect(() => { if (!prefs.tooltips) { clear(); setPosition(null); } }, [prefs.tooltips]);
    const close = () => { clear(); timer.current = setTimeout(() => setPosition(null), 100); };
    useEffect(() => {
        const hide = () => { clear(); setPosition(null); };
        window.addEventListener('scroll', hide, true);
        return () => { clear(); window.removeEventListener('scroll', hide, true); };
    }, []);
    return <span ref={host} className="issue-author" onMouseEnter={open} onMouseLeave={close} onFocus={open} onBlur={close} onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
    <IssueIdentity login={login} avatarUrl={avatarUrl}/>
    {position ? <IssuePortal><div className="issue-author-card" style={position} onMouseEnter={clear} onMouseLeave={close}><span className="identity"><span className="avatar">{login[0]?.toUpperCase()}</span><strong>{login}</strong></span>{stats ? <span className="muted">{stats.open} open · {stats.closed} closed issues</span> : null}<a href={`https://github.com/${encodeURIComponent(login)}`} target="_blank" rel="noopener noreferrer"><IssueIcon name="github"/>View GitHub profile</a></div></IssuePortal> : null}
  </span>;
}
