import { FilePlus2, Loader2, Paperclip, Plus, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { uploadWebChatArtifact } from "../../api/client";
import type { WebChatArtifact, WebChatToolCatalog } from "../../api/types";
import { WebChatResourceMenu, type WebChatDrawerKind } from "./WebChatResourceMenu";

function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Unable to read file"));
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.slice(value.indexOf(",") + 1) : value);
    };
    reader.readAsDataURL(file);
  });
}

export function WebChatAttachments({
  artifacts,
  conversationId,
  disabled,
  onChange,
  onError,
  onUploadingChange,
  onReferenceTask,
  onReferenceArtifact,
  plans = [],
  activeDrawer,
  activityCount,
  artifactCount,
  onDrawerChange,
  toolCatalog,
  projectId,
  threadId,
}: {
  artifacts: WebChatArtifact[];
  conversationId: string;
  disabled?: boolean;
  onChange: (items: WebChatArtifact[]) => void;
  onError: (message: string) => void;
  onUploadingChange?: (uploading: boolean) => void;
  onReferenceTask?: () => void;
  onReferenceArtifact?: () => void;
  plans?: Array<{ id: string; label: string; onSelect: () => void }>;
  activeDrawer: WebChatDrawerKind;
  activityCount: number;
  artifactCount: number;
  onDrawerChange: (kind: WebChatDrawerKind) => void;
  toolCatalog?: WebChatToolCatalog;
  projectId: string;
  threadId: string;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const query = search.trim().toLowerCase();
  const matches = (value: string) => !query || value.toLowerCase().includes(query);

  useEffect(() => {
    if (!open) return undefined;
    menuRef.current?.querySelector<HTMLInputElement>('[role="dialog"] input')?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); triggerRef.current?.focus(); }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    onUploadingChange?.(true);
    onError("");
    const next = [...artifacts];
    try {
      for (const file of Array.from(files)) {
        const result = await uploadWebChatArtifact(projectId, {
          logical_name: `attachments/${file.name}`,
          media_type: file.type || "application/octet-stream",
          data_base64: await fileBase64(file),
          lineage: { conversation_id: conversationId, thread_id: threadId },
        });
        if (!next.some((item) => item.version_id === result.artifact.version_id)) {
          next.push(result.artifact);
        }
        onChange([...next]);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      onUploadingChange?.(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="webchat-attachments">
      <input
        accept=".txt,.md,.json,.pdf,.png,.jpg,.jpeg,.gif,.zip"
        aria-label="Attach files"
        hidden
        multiple
        ref={inputRef}
        type="file"
        onChange={(event) => void upload(event.target.files)}
      />
      <div className="webchat-resource-menu" ref={menuRef}>
        <button aria-label="Add to message" aria-expanded={open} aria-haspopup="dialog"
          className="webchat-composer-icon" disabled={disabled || uploading} ref={triggerRef}
          title="Add to message" type="button" onClick={() => { setSearch(""); setOpen((current) => !current); }}>
          {uploading ? <Loader2 className="spin" size={16} /> : <Plus size={16} />}
        </button>
        {open ? <div className="webchat-resource-popover" role="dialog" aria-label="Add to message">
          <label className="webchat-resource-search"><Search size={15} aria-hidden="true" /><input value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Search attachments, context and tools" placeholder="Search attachments, context and tools" /></label>
          {!query ? <div className="webchat-resource-menu-title">Add to message</div> : null}
          {matches("Upload file attachment") ? <button type="button" onClick={() => { setOpen(false); inputRef.current?.click(); }}>
            <Paperclip size={16} /><span><strong>Upload file</strong></span>
          </button> : null}
          {matches("Reference selected task current task") ? <button type="button" disabled={!onReferenceTask} onClick={() => { setOpen(false); onReferenceTask?.(); }}>
            <FilePlus2 size={16} /><span><strong>Reference selected task</strong><small>{onReferenceTask ? "Use current task" : "Select a task first"}</small></span>
          </button> : null}
          {matches("Reference artifact") ? <button type="button" disabled={!onReferenceArtifact} onClick={() => { setOpen(false); onReferenceArtifact?.(); }}>
            <FilePlus2 size={16} /><span><strong>Reference artifact</strong></span>
          </button> : null}
          {plans.length ? plans.filter((plan) => matches(`Reference plan ${plan.label}`)).map((plan) => (
            <button key={plan.id} type="button" onClick={() => { setOpen(false); plan.onSelect(); }}>
              <FilePlus2 size={16} /><span><strong>Reference plan</strong><small>{plan.label}</small></span>
            </button>
          )) : matches("Reference plan") ? <button type="button" disabled><FilePlus2 size={16} /><span><strong>Reference plan</strong><small>No plan in this conversation</small></span></button> : null}
          <WebChatResourceMenu active={activeDrawer} activityCount={activityCount} artifactCount={artifactCount}
            query={query} toolCatalog={toolCatalog} onChange={(kind) => { setOpen(false); onDrawerChange(kind); }} />
        </div> : null}
      </div>
      {artifacts.map((item) => (
        <span className="webchat-attachment-chip" key={item.version_id}>
          <FilePlus2 size={13} />
          <span>{item.logical_name.split("/").pop()}</span>
          <button
            aria-label={`Remove ${item.logical_name}`}
            disabled={disabled || uploading}
            type="button"
            onClick={() => onChange(artifacts.filter((candidate) => candidate.version_id !== item.version_id))}
          >
            <X size={12} />
          </button>
        </span>
      ))}
    </div>
  );
}
