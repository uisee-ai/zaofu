import type { SVGProps } from "react";
/** Mechanically extracted from the approved Issue workspace SVG symbols. No icon-library substitutions. */
const paths = {
    "search": <><circle cx="11" cy="11" r="7"/><path d="m20 20-3.4-3.4"/></>,
    "refresh": <><path d="M20 6v5h-5"/><path d="M4 18v-5h5"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 9M5.5 15A7 7 0 0 0 18 17.5l2-2.5"/></>,
    "github": <><path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3.3-.4 6.8-1.6 6.8-7.4A5.8 5.8 0 0 0 19.3 3 5.4 5.4 0 0 0 19.1.9S17.9.5 15 2.5a13.4 13.4 0 0 0-7 0C5.1.5 3.9.9 3.9.9A5.4 5.4 0 0 0 3.7 3a5.8 5.8 0 0 0-1.5 4.1c0 5.8 3.5 7 6.8 7.4A4.8 4.8 0 0 0 8 18v4"/><path d="M8 19c-3 .9-3-1.5-4-2"/></>,
    "star": <><path d="m12 2 3 6 6.5 1-4.7 4.6 1.1 6.5L12 17l-5.9 3.1 1.1-6.5L2.5 9 9 8z"/></>,
    "external": <><path d="M15 3h6v6"/><path d="m10 14 11-11"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></>,
    "play": <><path d="m7 4 13 8-13 8z"/></>,
    "xcircle": <><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/></>,
    "chevron": <><path d="m6 9 6 6 6-6"/></>,
    "clock": <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    "dot": <><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/></>,
    "warn": <><path d="M10.3 3.5 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.5a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 3h.01"/></>,
    "wrench": <><path d="M14.7 6.3a4 4 0 0 0-5-5L12 3.6 8.4 7.2 6.1 4.9a4 4 0 0 0 5 5L19 17.8a2 2 0 1 1-2.8 2.8l-7.9-7.9"/></>,
    "pr": <><circle cx="6" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M6 8v10m12-2V9a3 3 0 0 0-3-3h-3"/><path d="m14 4-2 2 2 2"/></>,
    "shield": <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/></>,
    "x": <><path d="m6 6 12 12M18 6 6 18"/></>,
    "sort": <><path d="M3 6h13M3 12h9M3 18h5"/><path d="m17 14 4 4 4-4" transform="translate(-2)"/></>,
    "arrow-up": <><path d="M12 20V4"/><path d="m6 10 6-6 6 6"/></>,
    "arrow-down": <><path d="M12 4v16"/><path d="m6 14 6 6 6-6"/></>,
    "user": <><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></>,
    "clear": <><path d="M4 4h16M9 4V2h6v2m-9 0 1 17h10l1-17"/></>,
    "check": <><path d="m5 12 4 4L19 6"/></>,
    "pin": <><path d="M9 3h6l-1 6 4 4v2H6v-2l4-4-1-6Z"/><path d="M12 15v6"/></>,
    "info": <><circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/></>,
    "sliders": <><path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5"/><circle cx="16" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="13" cy="18" r="2"/></>,
    "pause": <><path d="M8 5v14M16 5v14"/></>,
    "route": <><circle cx="5" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 6h4a3 3 0 0 1 3 3v6a3 3 0 0 0 3 3"/><path d="M5 8v8a2 2 0 0 0 2 2h3"/></>,
    "flip": <><path d="M20 7a8 8 0 0 0-14.8-2"/><path d="M5 2v4h4"/><path d="M4 17a8 8 0 0 0 14.8 2"/><path d="M19 22v-4h-4"/></>,
} as const;
export type IssueIconName = keyof typeof paths;
export interface IssueIconProps extends Omit<SVGProps<SVGSVGElement>, "name" | "children"> {
    name: IssueIconName;
    size?: "sm" | "md" | "lg";
}
export function IssueIcon({ name, size = "sm", className = "", ...props }: IssueIconProps) {
    return (<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={`icon${size === "md" ? "" : ` ${size}`} ${className}`.trim()} {...props}>
      {paths[name]}
    </svg>);
}
