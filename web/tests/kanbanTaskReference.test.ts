import { referencedTaskId, withImplicitTaskReference } from "../src/components/orchestrator/kanbanTaskReference.js";

function assert(condition: unknown, message: string): void { if (!condition) throw new Error(message); }

assert(referencedTaskId(false, "TASK-1") === undefined, "removed context must not be submitted by chat or Plan");
assert(referencedTaskId(true, "TASK-1") === "TASK-1", "visible task is included");
assert(!("task_id" in withImplicitTaskReference({}, "update-task", false, "TASK-1")), "direct and proposal actions must not restore a removed task");
assert(withImplicitTaskReference({}, "update-task", true, "TASK-1").task_id === "TASK-1", "referenced task is available to actions");
assert(!("task_id" in withImplicitTaskReference({}, "create-task", true, "TASK-1")), "create task does not implicitly target the current task");
assert(withImplicitTaskReference({ task_id: "TASK-2" }, "update-task", false, "TASK-1").task_id === "TASK-2", "explicitly specified action targets remain intact");
