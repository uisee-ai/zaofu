import { Bell, FileCheck2, Inbox, MessageSquare } from "lucide-react";
import type { OperatorInboxItem } from "../../api/types";
import { useUiPreferences } from "../../app/UiPreferences";
import { formatDateTime } from "../../lib/uiPreferences";

export function InboxQueueItem({ item, selected, kind, primaryRef, onSelect }: { item: OperatorInboxItem; selected: boolean; kind: string; primaryRef: string; onSelect: () => void }) {
  const { t, preferences } = useUiPreferences();
  const timestamp = item.created_ts || item.resolved_ts;
  const Icon = item.kind.includes("approval") ? FileCheck2 : item.kind.includes("decision") || item.kind.includes("question") ? MessageSquare : Bell;
  return <div role="listitem"><button type="button" className={`inbox-queue-item ${selected ? "selected" : ""}`} aria-pressed={selected} data-testid="inbox-queue-item" data-inbox-id={item.id} onClick={onSelect}>
    <i className={`inbox-pending-dot ${item.status === "pending" ? "pending" : ""}`} aria-label={t(item.status)} />
    <span className="inbox-queue-avatar" title={t(kind)}><Icon size={14} aria-hidden="true" /></span>
    <span className="inbox-queue-copy"><strong>{item.title || t(kind)}</strong><span className="inbox-queue-summary">{item.summary || primaryRef}</span>
      <span className="inbox-queue-bottom"><span title={primaryRef}>{primaryRef || t(kind)}</span><span aria-hidden="true">·</span><time dateTime={timestamp} title={formatDateTime(timestamp, preferences)}>{formatDateTime(timestamp, preferences, true)}</time></span>
    </span><Inbox className="inbox-queue-kind" size={13} aria-hidden="true" />
  </button></div>;
}
