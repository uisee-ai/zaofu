import { HelpCircle, ShieldCheck } from "lucide-react";
import type { WebChatInteraction } from "../../api/types";
import { WebChatInteractionPanel } from "./WebChatInteractionPanel";
import {
  emptyInteractionDraft,
  interactionKey,
  interactionTitle,
  type WebChatInteractionDraft,
} from "./webChatInteractionModel";

export function WebChatInteractionCards({
  activeKey,
  busyKey = "",
  drafts,
  errors,
  interactions,
  onCompose,
  onDraftChange,
  onRespond,
}: {
  activeKey: string;
  busyKey?: string;
  drafts: Record<string, WebChatInteractionDraft>;
  errors: Record<string, string>;
  interactions: WebChatInteraction[];
  onCompose: (interaction: WebChatInteraction) => void;
  onDraftChange: (interaction: WebChatInteraction, draft: WebChatInteractionDraft) => void;
  onRespond: (interaction: WebChatInteraction, response: Record<string, unknown>) => void;
}) {
  if (!interactions.length) return null;
  return (
    <section aria-label="Pending agent interactions" className="webchat-interactions">
      {interactions.map((interaction) => {
        const key = interactionKey(interaction);
        if (key !== activeKey) {
          return (
            <WebChatInteractionPanel
              busy={busyKey === key}
              draft={drafts[key] ?? emptyInteractionDraft()}
              error={errors[key] ?? ""}
              interaction={interaction}
              key={key}
              mode="thread"
              onDraftChange={(draft) => onDraftChange(interaction, draft)}
              onExpand={() => onCompose(interaction)}
              onRespond={(response) => onRespond(interaction, response)}
            />
          );
        }
        return (
          <article className="webchat-interaction-record active" key={key} data-plan-request-id={String(interaction.request.request_event_id || "") || undefined}>
            <span className="webchat-interaction-icon" aria-hidden="true">
              {interaction.kind === "ask_user" ? <HelpCircle size={16} /> : <ShieldCheck size={16} />}
            </span>
            <span>
              <small>Waiting for you</small>
              <strong>{interactionTitle(interaction)}</strong>
            </span>
            <button type="button" onClick={() => onCompose(interaction)}>Answering below</button>
          </article>
        );
      })}
    </section>
  );
}
