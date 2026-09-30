// Test-only entry: the production build never imports this module or its data.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "../../../src/styles.css";
import "../../../src/cockpit.css";
import { IssueTriagePage, type TriageTab } from "../../../src/pages/IssueTriagePage";
import { IssueNotificationStack, useIssueNotifications } from "../../../src/pages/IssueTriageNotifications";
import "./fixture.css";
function Fixture() {
  const [tab, setTab] = useState<TriageTab>("github");
  const [projectId, setProjectId] = useState("fixture");
  const notices = useIssueNotifications();
  Object.assign(window, { issueFixture: { notify: notices.notify, dismiss: notices.dismiss, setProjectId } });
  return <><IssueTriagePage projectId={projectId} tab={tab} onTabChange={setTab}
    runtimeInterventions={<div>Runtime interventions (unchanged)</div>}
    onOpenTask={id => { Object.assign(window, { lastIssueNavigation: ["task", id] }); }}
    onOpenProjection={(kind, id) => { Object.assign(window, { lastIssueNavigation: [kind, id] }); }} />
    <IssueNotificationStack items={notices.items} onDismiss={notices.dismiss} /></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
