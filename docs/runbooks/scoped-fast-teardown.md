# Scoped Fast Teardown

Use `zf stop --fast` for scoped runtime teardown.

Do not use `pkill` or process-name based termination for ZaoFu teardown. Scope
shutdown to the configured project state and session metadata so unrelated
developer processes are not touched.

No script should append directly to `events.jsonl`. Teardown evidence and
state changes must go through the sanctioned CLI/runtime paths.
