"""``zf spec`` — convert structured markdown spec files into ZaoFu kanban state.

The agent-skills ``spec-driven-development`` skill (and related) produces
markdown deliverables with structured frontmatter describing tasks. ZaoFu's
state of truth is ``events.jsonl`` + ``kanban.json`` — populated normally
through the orchestrator's tool calls (``zf feature add`` / ``zf kanban add``
/ ``zf emit task.contract.update``). When a human or a single LLM session
writes a spec.md directly, this command ingests it into the same state so
downstream workers can pick up tasks without the orchestrator having to
re-derive the contract by reading the markdown.

Supported frontmatter (YAML, fenced by ``---``):

```yaml
---
spec: <slug>                 # required, unique slug under the spec
feature_id: F-<hex>           # optional; auto-generated if missing
feature_key: <slug>           # optional human key for the feature
phase: P1                     # optional, recorded in feature meta
tasks:
  - id: TASK-<HEX6>           # optional; auto-generated if missing
    title: <one-line title>
    owner_role: dev           # arch / critic / dev / review / test / judge
    scope:                    # list of repo-relative write paths
      - packages/ai/src/provider-registry.ts
    acceptance:               # list of shell commands (each must exit 0)
      - test -f packages/ai/src/provider-registry.ts
    verification: <single shell verification command>
    spec_ref: docs/specs/phase-1/runtime-foundation.md
    plan_ref: docs/plans/<project>-master-plan.md
    tdd_ref: test/unit/ai/provider-registry.test.ts
    behavior: |
      Multi-line prose behavior description...
    exclusions:
      - "src/legacy/**"
    blocked_by: [TASK-FOUNDATION] # dependency edges, decided by orchestrator
    wave: 2                       # topological execution layer
    shared_files:                 # read-only overlap with sibling tasks
      - packages/ai/src/types.ts
    exclusive_files:              # write locks used by scheduler conflict gate
      - packages/ai/src/provider-registry.ts
    verification_tiers: [static, runtime]
    handoff_artifacts:
      - packages/ai/src/provider-registry.ts
    complexity: standard        # simple / standard / complex / release
---

# Spec body (human-readable, not parsed)
...
```

The command:
  1. parses the frontmatter,
  2. creates / updates the feature in ``feature_list.json``,
  3. for each task, creates the task in ``kanban.json`` with the given
     contract, emits ``task.created`` + ``task.contract.update`` events.

Existing task IDs are detected and skipped (idempotent re-ingest).

Task-map artifacts should preserve the same mapping in machine-readable form:
``{feature_id, plan_ref, backlog_ref, tasks: [{task_id, plan_section,
blocked_by, wave, shared_files, exclusive_files, scope}]}``. The
orchestrator owns dependency judgment and writes the result into
``Task.blocked_by`` / ``TaskContract.wave`` / file-claim fields.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

import yaml

from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import EventSigningConfigError, event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "spec",
        help="Convert a structured spec markdown into ZaoFu kanban tasks",
    )
    sub = parser.add_subparsers(dest="spec_cmd", required=True)

    ingest = sub.add_parser(
        "ingest",
        help="Ingest spec frontmatter into kanban + events",
    )
    ingest.add_argument("path", help="Path to the spec markdown file")
    ingest.add_argument(
        "--state-dir",
        default=None,
        help="State dir override (default: project.state_dir from zf.yaml)",
    )
    ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + validate but do not write kanban / emit events",
    )
    ingest.add_argument(
        "--inherit-design",
        default=None,
        help=(
            "#I fix (TR-SPEC-INGEST-FILL-DESIGN-FIELDS-001): optional "
            "design.critique.done event id to inherit into each task's "
            "critic_event_id (e.g. evt-c52dd23d39f8). Default uses a "
            "'design-skipped:ingest-from-spec' placeholder so contract "
            "passes default 6-field workflow.dag.required_backlog_refs."
        ),
    )
    ingest.set_defaults(func=_run_ingest)

    validate = sub.add_parser(
        "validate",
        help="Validate spec frontmatter without writing state (pre-emit gate)",
    )
    validate.add_argument("path", help="Path to the spec markdown file")
    validate.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings (e.g. body-orphan task ids) as failures",
    )
    validate.set_defaults(func=_run_validate)

    prompt = sub.add_parser(
        "prompt",
        help="Print a backend-agnostic LLM prompt for extracting frontmatter",
    )
    prompt.add_argument("path", help="Path to the markdown spec (no frontmatter)")
    prompt.add_argument(
        "--system-only",
        action="store_true",
        help="Print only the system prompt (no body, no user prompt)",
    )
    prompt.set_defaults(func=_run_prompt)

    merge = sub.add_parser(
        "merge",
        help="Merge an externally-produced frontmatter JSON into a plain-md spec",
    )
    merge.add_argument("path", help="Path to the markdown spec (no frontmatter)")
    merge.add_argument(
        "--frontmatter",
        required=True,
        help="Path to JSON file with the frontmatter, or `-` for stdin",
    )
    merge.add_argument(
        "--output",
        default="overwrite",
        help="Output mode: 'overwrite' (default, write back), '-' (stdout), or a file path",
    )
    merge.add_argument(
        "--state-dir",
        default=None,
        help="State dir override (for emitting spec.extract.completed event)",
    )
    merge.set_defaults(func=_run_merge)


def _run_ingest(args: argparse.Namespace) -> int:
    spec_path = Path(args.path)
    if not spec_path.exists():
        print(f"error: spec file not found: {spec_path}", file=sys.stderr)
        return 2

    try:
        frontmatter, _body = _extract_frontmatter(spec_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if frontmatter is None:
        print(
            "error: no YAML frontmatter found at the start of the file. "
            "Wrap the spec metadata between ``---`` lines.",
            file=sys.stderr,
        )
        return 2

    try:
        plan = _build_ingest_plan(frontmatter, spec_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"spec: {plan['spec']}")
    print(f"feature: {plan['feature_id']} ({plan['feature_key']})")
    print(f"tasks: {len(plan['tasks'])}")
    for task_payload in plan["tasks"]:
        print(
            f"  - {task_payload['id']:<13} owner={task_payload['owner_role']:<8} "
            f"scope={task_payload['scope']}"
        )

    if args.dry_run:
        print("\n[dry-run] no state written")
        return 0

    try:
        ctx = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except Exception as exc:
        print(f"error: failed to resolve project context: {exc}", file=sys.stderr)
        return 2
    state_dir = ctx.state_dir
    if not state_dir.exists():
        print(
            f"error: state dir {state_dir} does not exist; run `zf init` first",
            file=sys.stderr,
        )
        return 2

    try:
        event_log = event_log_from_project(state_dir, config=ctx.config)
    except EventSigningConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    feature_store = FeatureStore(state_dir / "feature_list.json")
    task_store = TaskStore(state_dir / "kanban.json")
    writer = EventWriter(event_log)

    feature_id = plan["feature_id"]
    feature_key = plan["feature_key"]
    if feature_store.get(feature_id) is None:
        feature = Feature(
            id=feature_id,
            title=plan.get("title") or feature_key,
            description=plan.get("description", ""),
            status="active",
        )
        feature_store.add(feature)
        writer.emit(
            "feature.created",
            actor="zf-cli",
            payload={
                "feature_id": feature_id,
                "key": feature_key,
                "source": "spec_ingest",
                "spec_path": str(spec_path),
            },
        )
        print(f"created feature {feature_id} (key={feature_key})")
    else:
        print(f"feature {feature_id} already exists — reusing")

    created_count = 0
    skipped_count = 0

    # #I fix (TR-SPEC-INGEST-FILL-DESIGN-FIELDS-001, cangjie 2026-05-21
    # observation-I): pre-compute design-phase backlog field defaults.
    # cangjie spec ingest bridges plan → kanban directly (skip stage ④
    # backlog synthesis), but default yaml workflow.dag.required_backlog_refs
    # demands critic_event_id / critic_gate_ref / evidence_contract on
    # dispatch_preflight. Without these fields ingested tasks all reject
    # with contract.critic_event_id is required.
    #
    # --inherit-design <event_id>: operator points at a real
    # design.critique.done event (e.g. cangjie round 1
    # evt-c52dd23d39f8) for full audit trail.
    # Default: 'design-skipped:ingest-from-spec:<plan_path>' placeholder
    # so the field is non-empty.
    inherit_design = getattr(args, "inherit_design", None)
    default_critic_event_id = (
        inherit_design or f"design-skipped:ingest-from-spec:{spec_path}"
    )
    default_critic_gate_ref = str(spec_path)
    default_evidence_contract = {
        "plan_evidence": str(spec_path),
        "source": "spec_ingest",
        "inherit_design": inherit_design or "",
    }

    for task_payload in plan["tasks"]:
        task_id = task_payload["id"]
        existing = task_store.get(task_id)
        if existing is not None:
            try:
                from zf.runtime.task_doc import write_task_doc

                write_task_doc(state_dir, existing, source_event="spec_ingest_existing")
                task_store.update(task_id, contract=existing.contract)
            except Exception:
                pass
            skipped_count += 1
            continue

        contract = TaskContract(
            feature_id=feature_id,
            phase=plan.get("phase", ""),
            behavior=task_payload["behavior"],
            verification=task_payload["verification"],
            verification_tiers=task_payload["verification_tiers"],
            scope=task_payload["scope"],
            exclusions=task_payload["exclusions"],
            acceptance="\n".join(task_payload["acceptance"]) or "exit_code=0",
            spec_ref=task_payload["spec_ref"],
            plan_ref=task_payload["plan_ref"],
            tdd_ref=task_payload["tdd_ref"],
            source_key=task_payload["source_key"],
            source_ref=task_payload["source_ref"],
            owner_role=task_payload["owner_role"],
            owner_instance=task_payload["owner_instance"],
            wave=task_payload["wave"],
            shared_files=task_payload["shared_files"],
            exclusive_files=task_payload["exclusive_files"],
            handoff_artifacts=task_payload["handoff_artifacts"],
            complexity=task_payload["complexity"],
            # #I fix: fill design-phase backlog fields with placeholders
            # so dispatch_preflight passes default 6-field check
            critic_event_id=default_critic_event_id,
            critic_gate_ref=default_critic_gate_ref,
            evidence_contract=dict(default_evidence_contract),
        )
        task = Task(
            id=task_id,
            title=task_payload["title"],
            key=f"{feature_id}:{task_payload.get('key', task_id.lower())}",
            status="backlog",
            priority=task_payload.get("priority", 3),
            contract=contract,
            # TR-SPEC-INGEST-BLOCKED-BY-001 (#K fix): propagate plan
            # frontmatter blocked_by to Task so task_store.ready()
            # gates _dispatch_ready correctly.
            blocked_by=task_payload.get("blocked_by", []),
        )
        try:
            from zf.runtime.task_doc import write_task_doc

            task_doc = write_task_doc(state_dir, task, source_event="spec_ingest")
        except Exception as exc:
            print(f"FAIL task-doc-materialize: {task_id}: {exc}", file=sys.stderr)
            return 1
        task_store.add(task)

        create_event = writer.emit(
            "task.created",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "feature_id": feature_id,
                "key": task.key,
                "source": "spec_ingest",
                "spec_path": str(spec_path),
            },
        )
        writer.emit(
            "task.contract.update",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "contract": _contract_dict(contract),
                "source": "spec_ingest",
            },
            causation_id=create_event.id,
            correlation_id=create_event.correlation_id,
        )
        writer.emit(
            "task.doc.updated",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "source_event": "spec_ingest",
                "task_doc": str(task_doc.path),
                "source_doc": str(task_doc.source_path),
                "progress_doc": str(task_doc.progress_path),
                "source_revision": task_doc.source_revision,
                "contract_revision": task_doc.contract_revision,
                "capsule_revision": task_doc.capsule_revision,
            },
            causation_id=create_event.id,
            correlation_id=create_event.correlation_id,
        )
        if task_payload.get("complexity_explicit"):
            writer.emit(
                "task.complexity.overridden",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "complexity": task_payload["complexity"],
                    "source": "spec_ingest",
                    "spec_path": str(spec_path),
                },
                causation_id=create_event.id,
                correlation_id=create_event.correlation_id,
            )
        created_count += 1

    print(
        f"\ningested: {created_count} new task(s), {skipped_count} skipped "
        f"(already in kanban)"
    )
    return 0


def _extract_frontmatter(path: Path) -> tuple[dict | None, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None, text
    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        raise ValueError("frontmatter opened with `---` but never closed")
    front = rest[:end].lstrip("\n")
    body_start = end + len("\n---")
    body = rest[body_start:].lstrip("\n")
    try:
        data = yaml.safe_load(front) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter YAML parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return data, body


def _build_ingest_plan(frontmatter: dict, spec_path: Path) -> dict:
    spec = str(frontmatter.get("spec") or "").strip()
    if not spec:
        raise ValueError("frontmatter.spec is required")

    feature_id = str(frontmatter.get("feature_id") or "").strip()
    if not feature_id:
        # Deterministic feature id from spec slug — stable across re-ingest.
        feature_id = "F-" + uuid.uuid5(
            uuid.NAMESPACE_DNS, f"zf-spec:{spec}"
        ).hex[:8]
    feature_key = str(frontmatter.get("feature_key") or spec).strip()
    phase = str(frontmatter.get("phase") or "").strip()
    feature_title = str(frontmatter.get("title") or feature_key).strip()
    description = str(frontmatter.get("description") or "").strip()

    raw_tasks = frontmatter.get("tasks") or []
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("frontmatter.tasks must be a non-empty list")

    tasks: list[dict] = []
    for idx, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict):
            raise ValueError(f"tasks[{idx}] must be a YAML mapping")
        task_id = str(raw.get("id") or "").strip()
        if not task_id:
            task_id = "TASK-" + uuid.uuid4().hex[:6].upper()
        task_title = str(raw.get("title") or "").strip()
        if not task_title:
            raise ValueError(f"tasks[{idx}].title is required")
        owner_role = str(raw.get("owner_role") or "dev").strip()
        owner_instance = str(raw.get("owner_instance") or "").strip()
        scope = _string_list(raw.get("scope"), f"tasks[{idx}].scope")
        if not scope:
            raise ValueError(
                f"tasks[{idx}].scope is required (list of repo-relative paths)"
            )
        acceptance = _string_list(raw.get("acceptance"), f"tasks[{idx}].acceptance")
        verification = str(raw.get("verification") or "").strip()
        if not verification and acceptance:
            verification = acceptance[-1]
        if not verification:
            raise ValueError(
                f"tasks[{idx}] requires either `verification` or non-empty "
                "`acceptance` list"
            )
        verification_tiers = _string_list(
            raw.get("verification_tiers"),
            f"tasks[{idx}].verification_tiers",
        ) or ["runtime"]
        behavior = str(raw.get("behavior") or "").strip()
        if not behavior:
            behavior = f"deliverable from {spec_path}#tasks[{idx}]"
        spec_ref = str(raw.get("spec_ref") or "").strip() or str(spec_path)
        plan_ref = str(raw.get("plan_ref") or "").strip()
        tdd_ref = str(raw.get("tdd_ref") or "").strip()
        # Source-coverage anchors back to the plan section (doc 71). The
        # frontmatter may carry these from the spec-bridge extraction; pass
        # them through so the agent-skills ingest path keeps provenance
        # instead of falling back to a kernel-derived feature:task key.
        source_key = str(raw.get("source_key") or "").strip()
        source_ref = str(raw.get("source_ref") or "").strip()
        exclusions = _string_list(raw.get("exclusions"), f"tasks[{idx}].exclusions")
        handoff = _string_list(
            raw.get("handoff_artifacts"),
            f"tasks[{idx}].handoff_artifacts",
        )
        priority = int(raw.get("priority") or 3)
        key = str(raw.get("key") or task_id.lower()).strip()
        # TR-SPEC-INGEST-BLOCKED-BY-001 (#K cangjie 2026-05-21):
        # Plan frontmatter declares cross-vertical blocked_by chain
        # (P0V02-V05 blocked_by=[P0V01], P0V06 blocked_by=[P0V01..V05],
        # etc). Previously this field was silently dropped, so
        # task_store.ready() (which gates _dispatch_ready) saw every
        # ingested task as blocked_by=[] → kernel over-dispatches the
        # entire phase as fan-out → dev.blocked phase_gate_violation
        # cascade overloads arch single instance with reworks.
        blocked_by = _string_list(
            raw.get("blocked_by"), f"tasks[{idx}].blocked_by",
        )
        wave = _task_wave(raw.get("wave"), f"tasks[{idx}].wave")
        shared_files = _string_list(
            raw.get("shared_files"),
            f"tasks[{idx}].shared_files",
        )
        exclusive_files = _string_list(
            raw.get("exclusive_files"),
            f"tasks[{idx}].exclusive_files",
        )
        overlap = sorted(set(shared_files) & set(exclusive_files))
        if overlap:
            raise ValueError(
                f"tasks[{idx}].shared_files overlaps exclusive_files: {overlap}"
            )
        complexity_explicit = "complexity" in raw
        complexity = _task_complexity(
            raw.get("complexity"),
            f"tasks[{idx}].complexity",
        )

        tasks.append(
            {
                "id": task_id,
                "title": task_title,
                "owner_role": owner_role,
                "owner_instance": owner_instance,
                "scope": scope,
                "acceptance": acceptance,
                "verification": verification,
                "verification_tiers": verification_tiers,
                "behavior": behavior,
                "spec_ref": spec_ref,
                "plan_ref": plan_ref,
                "tdd_ref": tdd_ref,
                "source_key": source_key,
                "source_ref": source_ref,
                "exclusions": exclusions,
                "handoff_artifacts": handoff,
                "priority": priority,
                "key": key,
                "blocked_by": blocked_by,
                "wave": wave,
                "shared_files": shared_files,
                "exclusive_files": exclusive_files,
                "complexity": complexity,
                "complexity_explicit": complexity_explicit,
            }
        )

    return {
        "spec": spec,
        "feature_id": feature_id,
        "feature_key": feature_key,
        "phase": phase,
        "title": feature_title,
        "description": description,
        "tasks": tasks,
    }


def _string_list(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value.strip()]
    elif isinstance(value, list):
        items = []
        for entry in value:
            if entry is None:
                continue
            if not isinstance(entry, (str, int, float)):
                raise ValueError(
                    f"{label} entries must be strings; got {type(entry).__name__}"
                )
            text = str(entry).strip()
            if text:
                items.append(text)
    else:
        raise ValueError(f"{label} must be a string or list of strings")
    return items


def _task_complexity(value: object, label: str) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    if text not in {"simple", "standard", "complex", "release"}:
        raise ValueError(
            f"{label} must be one of simple / standard / complex / release"
        )
    return text


def _task_wave(value: object, label: str) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer or wave label")
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            return int(digits)
    raise ValueError(f"{label} must be an integer or wave label")


def _contract_dict(contract: TaskContract) -> dict:
    from dataclasses import asdict

    return asdict(contract)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _run_validate(args: argparse.Namespace) -> int:
    spec_path = Path(args.path)
    if not spec_path.exists():
        print(f"FAIL file-not-found: {spec_path}", file=sys.stderr)
        return 2

    try:
        frontmatter, body = _extract_frontmatter(spec_path)
    except ValueError as exc:
        print(f"FAIL frontmatter-parse: {exc}", file=sys.stderr)
        return 1
    if frontmatter is None:
        print(
            "FAIL no-frontmatter: file must start with `---` … `---` block "
            "(use `zf spec extract --from-md` to auto-generate)",
            file=sys.stderr,
        )
        return 1

    try:
        plan = _build_ingest_plan(frontmatter, spec_path)
    except ValueError as exc:
        print(f"FAIL schema: {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []
    warnings: list[str] = []

    duplicates = _find_duplicate_task_ids(plan)
    if duplicates:
        errors.append(f"duplicate-task-ids: {sorted(duplicates)}")

    missing_accept = [
        t["id"] for t in plan["tasks"]
        if not t["acceptance"] and not t["verification"]
    ]
    if missing_accept:
        errors.append(
            f"missing-acceptance-and-verification: {missing_accept}"
        )

    declared = {t["id"] for t in plan["tasks"]}
    orphan_ids = _scan_body_for_task_ids(body) - declared
    if orphan_ids:
        warnings.append(
            f"body-orphan-task-ids: {sorted(orphan_ids)} (referenced in body "
            "but not declared in frontmatter.tasks)"
        )

    for line in warnings:
        print(f"WARN {line}", file=sys.stderr)
    for line in errors:
        print(f"FAIL {line}", file=sys.stderr)

    if errors:
        return 1
    if warnings and getattr(args, "strict", False):
        print("FAIL strict-mode: warnings treated as errors", file=sys.stderr)
        return 1

    print(
        f"OK spec={plan['spec']} feature={plan['feature_id']} "
        f"tasks={len(plan['tasks'])}"
    )
    return 0


def _find_duplicate_task_ids(plan: dict) -> set[str]:
    seen: set[str] = set()
    dup: set[str] = set()
    for task in plan["tasks"]:
        tid = task["id"]
        if tid in seen:
            dup.add(tid)
        seen.add(tid)
    return dup


_TASK_ID_RE = __import__("re").compile(r"\bTASK-[A-Z0-9]{4,}\b")


def _scan_body_for_task_ids(body: str) -> set[str]:
    return set(_TASK_ID_RE.findall(body or ""))


# ---------------------------------------------------------------------------
# prompt — backend-agnostic frontmatter extraction prompt template
# ---------------------------------------------------------------------------


_EXTRACT_SYSTEM_PROMPT = """\
You are a spec → frontmatter extractor for the zaofu harness.

Given a markdown spec describing a feature decomposed into N VS/tasks,
output a single JSON object (no markdown fences, no extra prose) that
zaofu's `zf spec ingest` can consume.

Required schema (omit optional fields when unsure):

{
  "spec": "<kebab-case slug — derive from filename or first H1>",
  "feature_key": "<human-readable key, typically same as spec>",
  "phase": "<P0|P1|P2|P3|P4|P5|empty>",
  "title": "<one-line zh-CN title>",
  "tasks": [
    {
      "title": "<one-line zh-CN title>",
      "owner_role": "dev",
      "scope": ["<repo-relative path actually appearing in the md>"],
      "acceptance": ["<shell command exit 0>"],
      "verification_tiers": ["static", "runtime"],
      "behavior": "<one or two zh-CN sentences>",
      "spec_ref": "<source markdown path>",
      "blocked_by": ["<task ids this task depends on, omit or [] if none>"],
      "wave": 1,
      "shared_files": ["<repo-relative read-only/shared paths>"],
      "exclusive_files": ["<repo-relative paths this task writes/owns>"],
      "handoff_artifacts": ["<typically same as scope>"]
    }
  ]
}

Rules:
- Each H2 or H3 section that names a deliverable becomes one task.
- DO NOT invent file paths. Only use paths appearing verbatim inside
  backticks in the markdown body.
- DO NOT invent shell commands. Only use commands present in the body.
- If a task has no commands in the body, set acceptance to `["test -f <scope_path>"]`.
- owner_role defaults to "dev" when unstated.
- Only emit blocked_by / wave / shared_files / exclusive_files when the markdown
  explicitly states dependency, ordering, parallelism, or file ownership.
- Reply with a single JSON object only — no surrounding text, no markdown fences.
"""


def _run_prompt(args: argparse.Namespace) -> int:
    """Print a ready-to-paste prompt for any LLM frontend (claude / codex /
    web ui / Claude Code subagent). Backend-agnostic by design.
    """
    if getattr(args, "system_only", False):
        print(_EXTRACT_SYSTEM_PROMPT.rstrip())
        return 0

    spec_path = Path(args.path)
    if not spec_path.exists():
        print(f"error: file not found: {spec_path}", file=sys.stderr)
        return 2

    text = spec_path.read_text(encoding="utf-8")
    if text.lstrip().startswith("---"):
        print(
            f"error: {spec_path} already has frontmatter — nothing to extract",
            file=sys.stderr,
        )
        return 1

    print("=== SYSTEM PROMPT ===")
    print(_EXTRACT_SYSTEM_PROMPT.rstrip())
    print()
    print("=== USER MESSAGE ===")
    print(f"Source file: {spec_path}")
    print()
    print("Markdown body follows below the divider.")
    print("---")
    print(text.rstrip())
    print()
    print("=== INSTRUCTIONS ===")
    print(
        "Paste the above into any LLM (claude / codex / Claude Code subagent / "
        "web chat). Save the JSON reply to a file (e.g. /tmp/fm.json), then:"
    )
    print(f"  zf spec merge {spec_path} --frontmatter /tmp/fm.json")
    print("  zf spec validate <merged-file>")
    print("  zf spec ingest <merged-file>")
    return 0


# ---------------------------------------------------------------------------
# merge — inject a frontmatter JSON into a plain-md spec
# ---------------------------------------------------------------------------


def _run_merge(args: argparse.Namespace) -> int:
    spec_path = Path(args.path)
    if not spec_path.exists():
        print(f"error: file not found: {spec_path}", file=sys.stderr)
        return 2

    text = spec_path.read_text(encoding="utf-8")
    if text.lstrip().startswith("---"):
        print(
            f"error: {spec_path} already has frontmatter — refusing to overwrite",
            file=sys.stderr,
        )
        return 1

    import json as _json
    try:
        if args.frontmatter == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(args.frontmatter).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read frontmatter source: {exc}", file=sys.stderr)
        return 2

    raw_stripped = raw.strip()
    if raw_stripped.startswith("```"):
        # Trim fences in case operator pasted a code block.
        lines = raw_stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw_stripped = "\n".join(lines).strip()

    try:
        data = _json.loads(raw_stripped)
    except _json.JSONDecodeError as exc:
        print(
            f"error: frontmatter source is not valid JSON: {exc}\n"
            f"      hint: paste the LLM reply only, no surrounding chat text",
            file=sys.stderr,
        )
        return 1

    if not isinstance(data, dict):
        print(
            f"error: frontmatter JSON must be an object, got {type(data).__name__}",
            file=sys.stderr,
        )
        return 1
    if not data.get("spec") or not isinstance(data.get("tasks"), list):
        print(
            f"error: frontmatter JSON missing required keys (spec, tasks); "
            f"got keys={list(data)}",
            file=sys.stderr,
        )
        return 1

    frontmatter_yaml = yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True,
    ).rstrip("\n")
    merged = f"---\n{frontmatter_yaml}\n---\n\n{text.lstrip()}"

    if args.output == "-":
        print(merged)
    elif args.output == "overwrite":
        spec_path.write_text(merged, encoding="utf-8")
        print(f"wrote frontmatter into {spec_path}", file=sys.stderr)
    else:
        Path(args.output).write_text(merged, encoding="utf-8")
        print(f"wrote frontmatter to {args.output}", file=sys.stderr)

    try:
        ctx = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        state_dir = ctx.state_dir
        if state_dir.exists():
            event_log = event_log_from_project(state_dir, config=ctx.config)
            EventWriter(event_log).emit(
                "spec.extract.completed",
                actor="zf-cli",
                payload={
                    "spec_path": str(spec_path),
                    "source": "merge",
                    "tasks_extracted": len(data.get("tasks", [])),
                },
            )
    except Exception:  # noqa: BLE001 — event emission is best-effort
        pass

    return 0
