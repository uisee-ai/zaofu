import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { useUiPreferences } from "../../app/UiPreferences";
export function TaskDrawer({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const { t } = useUiPreferences();
  useEffect(() => {
    const element = dialog.current;
    if (open && element && !element.open) element.showModal();
    if (!open && element?.open) element.close();
    return () => { if (element?.open) element.close(); };
  }, [open]);
  return <dialog className="task-detail-drawer" ref={dialog} aria-label={t("Task details")} onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget) { const box = event.currentTarget.getBoundingClientRect(); if (event.clientX < box.left) onClose(); } }}>
    <div className="task-drawer-head"><span>{t("Task details")}</span><button type="button" className="icon-button" aria-label={t("Close task details")} onClick={onClose}><X size={18} /></button></div>
    <div className="task-drawer-body">{open && children}</div>
  </dialog>;
}
