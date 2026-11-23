import { useState, type ComponentProps } from "react";
import { OrchestratorRoute } from "../../app/lazyRoutes";

type Props = Omit<ComponentProps<typeof OrchestratorRoute>, "visible" | "panelMode" | "onPanelModeChange" | "taskThread">;

export function TaskConversation(props: Props) {
  const [expanded, setExpanded] = useState(false);
  return <section className="reader-conversation" data-expanded={expanded}>
    <button type="button" className="reader-chat-size" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      {expanded ? "收起对话" : "展开对话"}
    </button>
    <OrchestratorRoute {...props} visible panelMode="docked"
      taskThread={`task:${props.context.taskId}`} onPanelModeChange={() => {}} />
  </section>;
}
