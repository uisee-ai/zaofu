import type { OperatorInboxItem } from "../../api/types";
import { useUiPreferences } from "../../app/UiPreferences";
import { formatDateTime } from "../../lib/uiPreferences";
export function InboxQueueItem({ item, selected, kind, primaryRef, onSelect }: { item: OperatorInboxItem; selected: boolean; kind: string; primaryRef: string; onSelect: () => void }) {
  const { t, preferences } = useUiPreferences();
  const timestamp = item.created_ts || item.resolved_ts;
  return <div role="listitem"><button type="button" className={`inbox-queue-item ${selected ? "selected" : ""}`} aria-pressed={selected} data-testid="inbox-queue-item" data-inbox-id={item.id} onClick={onSelect}>
    <span className="inbox-queue-top"><span><i className={`inbox-pending-dot ${item.status === "pending" ? "pending" : ""}`} aria-hidden="true" />{t(kind)}</span><time dateTime={timestamp} title={formatDateTime(timestamp, preferences)}>{formatDateTime(timestamp, preferences, true)}</time></span>
    <strong>{item.title || t(kind)}</strong><span className="inbox-queue-summary">{item.summary || primaryRef}</span>
    <span className="inbox-queue-bottom"><span className="mono">{primaryRef}</span><span>{t(item.status)}</span></span>
  </button></div>;
}
