import "../src/styles.css";
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { TaskInputPanel } from "../src/components/kanban/TaskInputPanel";
import { TaskSkillEvaluations } from "../src/components/kanban/TaskSkillEvaluations";
function Fixture() {
  const [project, setProject] = useState("first");
  return <><button onClick={() => setProject("second")}>切换项目</button>
    {window.location.search === "?evaluations"
      ? <TaskSkillEvaluations key={project} projectId={project} taskId="same-task" />
      : <TaskInputPanel key={project} projectId={project} attemptId="same-attempt" />}</>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
