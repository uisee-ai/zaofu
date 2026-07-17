import { useRef } from "react";

import { ProjectRequestScope } from "./projectRequestScope";

export function useProjectRequestScope(projectId: string): ProjectRequestScope {
  const scopeRef = useRef<ProjectRequestScope | null>(null);
  if (scopeRef.current === null) scopeRef.current = new ProjectRequestScope(projectId);
  scopeRef.current.activate(projectId);
  return scopeRef.current;
}
