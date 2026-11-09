export function referencedTaskId(referenced: boolean, taskId: string): string | undefined {
  return referenced && taskId ? taskId : undefined;
}

export function withImplicitTaskReference(
  payload: Record<string, unknown>, action: string, referenced: boolean, taskId: string,
): Record<string, unknown> {
  const result = { ...payload };
  const id = referencedTaskId(referenced, taskId);
  if (!("task_id" in result) && action !== "create-task" && id) result.task_id = id;
  return result;
}
