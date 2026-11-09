import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { TaskInputPanel } from "../src/components/kanban/TaskInputPanel";
function Fixture() {
  const [project, setProject] = useState("first");
  return <><button onClick={() => setProject("second")}>切换项目</button>
    <TaskInputPanel key={project} projectId={project} attemptId="same-attempt" /></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
