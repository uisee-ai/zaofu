import { lazy, Suspense, useState, type ComponentProps } from "react";
import { useUiPreferences } from "../../app/UiPreferences";

const GraphWorkbench = lazy(() => import("./WorkflowGraphWorkbench"));

export function WorkflowGraphDisclosure(props: ComponentProps<typeof GraphWorkbench>) {
  const { t } = useUiPreferences();
  const [open, setOpen] = useState(() => {
    const query = new URLSearchParams(location.search);
    return query.has("graph") || query.has("node");
  });
  return (
    <details className="workspace-disclosure workflow-graph-disclosure" open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{t("Workflow graph")}</summary>
      {open && <Suspense fallback={<p>{t("Loading…")}</p>}><GraphWorkbench {...props} /></Suspense>}
    </details>
  );
}
