import type { ComponentProps } from "react";
import { OrchestratorRoute } from "../../app/lazyRoutes";

type Props = Omit<ComponentProps<typeof OrchestratorRoute>, "visible" | "panelMode" | "onPanelModeChange" | "taskThread">;

export function TaskConversation(props: Props) {
  return <div className="reader-conversation">
    <OrchestratorRoute {...props} visible panelMode="docked"
      taskThread={`task:${props.context.taskId}`} onPanelModeChange={() => {}} />
  </div>;
}
