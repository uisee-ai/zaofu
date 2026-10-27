import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import "./previewAgent.css";

/** Shared inline reply shell for version-bound YAML and Plan reviews. */
export default function PreviewAgentReply({ anchor, open, onClose, label, children }: {
  anchor: HTMLElement | null; open: boolean; onClose: () => void;
  label: string; children: ReactNode;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!open || !anchor) return;
    dialog.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus({ preventScroll: true });
    let frame = 0;
    const keepVisible = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => dialog.current?.scrollIntoView({ block: "nearest" }));
    };
    const observer = new ResizeObserver(keepVisible);
    if (dialog.current) observer.observe(dialog.current);
    window.addEventListener("resize", keepVisible);
    keepVisible();
    const scope = anchor.parentElement;
    const selector = "[data-workflow-agent-trigger], [data-preview-agent-trigger]";
    const active = document.activeElement;
    const trigger = active instanceof HTMLButtonElement && scope?.contains(active) && active.matches(selector)
      ? active : scope?.querySelector<HTMLButtonElement>(selector);
    const outside = (event: MouseEvent) => {
      // React may remove a clicked Diff/quote button before this listener runs.
      const path = event.composedPath();
      if (path.some(node => node instanceof Element && node.classList.contains("workflow-quote-action"))) return;
      if (path.some(node => node instanceof Element && scope?.contains(node) && node.matches(selector))) return;
      if (!path.includes(anchor)) close.current();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close.current(); trigger?.focus(); }
    };
    document.addEventListener("click", outside);
    document.addEventListener("keydown", escape);
    return () => {
      observer.disconnect(); cancelAnimationFrame(frame);
      window.removeEventListener("resize", keepVisible);
      document.removeEventListener("click", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open, anchor]);
  return anchor && createPortal(<div ref={dialog} role="dialog" hidden={!open} className="workflow-agent-reply" aria-label={label}>
    <header><strong>Agent</strong><button type="button" className="workflow-agent-close" aria-label="Close agent chat" onClick={onClose}><X size={18} /></button></header>
    {children}
  </div>, anchor);
}
