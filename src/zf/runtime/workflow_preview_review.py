"""Version-bound Preview discussion and bounded, owner-applied revisions."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any
import uuid

import yaml

from zf.runtime.workflow_review_fields import apply_config_field, config_review_fields, flow_input_roles

from zf.core.config.loader import load_config
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_proposal import build_workflow_proposal, load_workflow_proposal
from zf.runtime.workflow_review_candidate import review_candidate
from zf.runtime.workflow_requests import (
    hydrate_workflow_requirement,
    load_workflow_request,
    revise_workflow_request,
)

SCHEMA = "workflow-preview-review.v1"


class PreviewReviewError(ValueError):
    def __init__(self, code: str, reason: str, status: int = 409):
        super().__init__(reason)
        self.code, self.status = code, status


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(value: Any) -> str:
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )


def _quote_range(original: str, quote: dict) -> tuple[int, int]:
    offset, end = quote.get("start"), quote.get("end")
    if offset is None and end is None and original.count(quote["text"]) == 1:
        offset = original.index(quote["text"])
        end = offset + len(quote["text"])
    if (
        type(offset) is not int
        or type(end) is not int
        or offset < 0
        or end <= offset
        or end > len(original)
        or original[offset:end] != quote["text"]
    ):
        raise PreviewReviewError(
            "ambiguous_reference",
            "Quote position is stale or ambiguous; select the text again",
            422,
        )
    return offset, end


def revise_preview_requirement(state_dir: Path, manifest_path: Path, **kwargs) -> dict:
    """Serialize the existing clarification surface with review/start actions."""
    with locked_path(Path(state_dir) / "locks" / "workflow-preview-mutation"):
        return revise_workflow_request(state_dir, manifest_path, **kwargs)


class WorkflowPreviewReview:
    def __init__(self, state_dir: Path, project_root: Path, writer: EventWriter):
        self.state_dir, self.project_root, self.writer = state_dir, project_root, writer
        self.config_path = project_root / "zf.yaml"

    def context(self, binding: dict, *, _request: dict | None = None,
                _proposal_ref: dict | None = None, _allow_started: bool = False) -> dict:
        request = _request or load_workflow_request(
            self.state_dir, str(binding.get("request_id") or "")
        )
        if not request:
            raise PreviewReviewError("not_found", "Workflow request not found", 404)
        from zf.runtime.run_admission import request_admission_view

        events = self.writer.event_log.read_all()
        admission = request_admission_view(
            events,
            request_id=request["request_id"],
            run_id=str(request.get("run_id") or ""),
        )
        read_only = bool(admission.get("run_id") or request.get("run_id"))
        if (
            request.get("status") not in {"ready", "proposed"}
            or read_only
        ) and not (_allow_started and read_only):
            raise PreviewReviewError(
                "review_not_pending",
                "Only an unstarted Preview can be revised; use controlled replan for an active Run",
            )
        if _proposal_ref is not None:
            proposal, proposal_ref = load_workflow_proposal(self.state_dir, _proposal_ref), _proposal_ref
        else:
            proposal, proposal_ref = review_candidate(self.state_dir, request, events)
        if (not proposal or proposal.get("request_id") != request["request_id"]
                or proposal.get("request_revision") != request["revision"]
                or proposal.get("requirement_spec_digest") != request["requirement_spec_digest"]):
            raise PreviewReviewError("stale_preview", "Preview changed; prepare a current candidate")
        if request.get("proposal_digest") and request["proposal_digest"] != proposal["proposal_digest"]:
            raise PreviewReviewError("stale_preview", "Preview changed; canonical binding differs")
        expected = {
            "request_id": request["request_id"],
            "revision": request["revision"],
            "proposal_digest": proposal["proposal_digest"],
        }
        if any(binding.get(k) != v for k, v in expected.items()):
            raise PreviewReviewError(
                "stale_preview",
                "Preview changed; refresh before discussing or applying",
            )
        if read_only:
            from zf.runtime.workflow_snapshot_yaml import project_workflow_yaml

            snapshot = hydrate_sidecar_ref(self.state_dir, proposal["effective_config_ref"]).payload
            config_text = project_workflow_yaml(
                snapshot, proposal, state_dir=self.state_dir, project_root=self.project_root,
            ).get("yaml")
            if not isinstance(config_text, str) or not config_text.strip():
                raise PreviewReviewError("snapshot_unavailable", "The bound Run configuration is unavailable")
            return {
                "binding": expected, "request": request, "proposal": proposal,
                "proposal_ref": proposal_ref, "read_only": True,
                "fields": {"yaml": {"value": config_text, "editable": False,
                    "reason": "运行快照已冻结；可以讨论，修改需新的审核提案。"}},
                "config_digest": proposal["effective_config_ref"]["sha256"],
                "config_text": config_text,
            }
        config_digest = _digest(self.config_path)
        if config_digest not in {
            proposal.get("base_config", {}).get("sha256"),
            proposal.get("target_config", {}).get("sha256"),
        }:
            raise PreviewReviewError(
                "stale_preview",
                "Config changed since this Preview; refresh the proposal",
            )
        requirement = hydrate_workflow_requirement(self.state_dir, request)
        manifest = json.loads(Path(request["workflow_input_manifest_ref"]).read_text())
        bound = bool(
            requirement.get("task_input_binding")
            or requirement.get("task_input_contract_ref")
            or manifest.get("task_input_binding")
            or manifest.get("task_input_contract")
            or request.get("origin_binding", {}).get("channel_id")
        )
        fields: dict[str, dict] = {}
        for name in ("objective", "acceptance", "constraints", "open_questions"):
            value = requirement.get(name)
            items = enumerate(value) if isinstance(value, list) else [(None, value)]
            for index, item in items:
                if not isinstance(item, str):
                    continue
                key = f"requirement.{name}" + (f".{index}" if index is not None else "")
                fields[key] = {
                    "value": item,
                    "editable": not bound,
                    "reason": "请回到绑定的 Task / Channel PRD 修订规范需求"
                    if bound
                    else "",
                }
        fields["execution_plan"] = {
            "value": proposal.get("stage_graph", {}),
            "editable": False,
            "reason": "阶段结构变更请使用受控配置提案；此处可引用讨论",
        }
        from zf.runtime.workflow_config_apply import WorkflowConfigApplyService

        candidate_path = WorkflowConfigApplyService(
            state_dir=self.state_dir, project_root=self.project_root
        )._private_candidate(proposal)
        if _digest(candidate_path) != str(
            proposal.get("target_config", {}).get("sha256") or ""
        ):
            raise PreviewReviewError(
                "invalid_config_candidate", "Preview config candidate digest mismatch"
            )
        config_text = candidate_path.read_text()
        compiled_config = load_config(candidate_path)
        from zf.runtime.workflow_review_roles import role_review_fields, role_review_inputs
        role_inputs_by_name = role_review_inputs(compiled_config)
        docs = list(yaml.safe_load_all(config_text))
        for index, doc in enumerate(docs):
            if isinstance(doc, dict) and doc.get("kind") == "ZfConfig":
                fields.update(role_review_fields(index, role_inputs_by_name))
                plan_approval = (doc.get("spec", {}).get("workflow") or {}).get("plan_approval")
                suffix = ".enabled" if isinstance(plan_approval, dict) else ""
                fields[f"config.{index}.workflow.plan_approval{suffix}"] = {
                    "value": compiled_config.workflow.plan_approval_enabled, "editable": True,
                    "reason": "计划生成后等待 Owner 审阅 PRD / Plan，再进入实施；需确认新版配置。",
                }
            if not isinstance(doc, dict) or doc.get("kind") not in {
                "IssueFlow",
                "PrdFlow",
                "RefactorFlow",
            }:
                continue
            family = str(proposal.get("flow_family") or "").lower().removesuffix("flow")
            if str(doc["kind"]).lower().removesuffix("flow") != family:
                continue
            spec = doc.get("spec") or {}
            role_inputs = flow_input_roles(docs, index, family, config_path=candidate_path)
            fields.update(config_review_fields(index, spec, role_names=list(role_inputs),
                family=family, inherited_skills=role_inputs))
        return {
            "read_only": False,
            "binding": expected,
            "request": request,
            "requirement": requirement,
            "proposal": proposal,
            "proposal_ref": proposal_ref,
            "manifest": manifest,
            "fields": fields,
            "config_digest": config_digest,
            "config_text": config_text,
            "compiled_role_skills": {role.name: list(role.skills) for role in compiled_config.roles},
            "role_inputs": role_inputs_by_name,
        }

    def public_context(self, binding: dict) -> dict:
        ctx = self.context(binding, _allow_started=True)
        return {k: ctx[k] for k in ("binding", "fields", "config_digest", "read_only")}

    def discuss(self, payload: dict, *, agent: Any = None) -> dict:
        ctx = self.context(payload, _allow_started=True)
        mode = str(payload.get("mode") or "discuss")
        if mode not in {"chat", "discuss", "revise"}:
            raise PreviewReviewError("invalid_mode", "Choose chat, discuss or revise", 422)
        if ctx["read_only"] and mode == "revise":
            raise PreviewReviewError("snapshot_read_only", "Run snapshots support discussion only; prepare a new proposal to edit")
        field = str(payload.get("field") or "")
        selected = ctx["fields"].get(field)
        if selected is None and (mode != "chat" or field or payload.get("quote")):
            raise PreviewReviewError(
                "invalid_reference", "Select a current Preview field", 422
            )
        quote = payload.get("quote")
        if quote is not None:
            if (
                not isinstance(quote, dict)
                or not isinstance(quote.get("text"), str)
                or not quote["text"]
                or quote["text"] not in _text(selected["value"])
            ):
                raise PreviewReviewError(
                    "invalid_reference",
                    "Quoted text no longer belongs to the selected field",
                    422,
                )
        if quote is not None:
            _quote_range(_text(selected["value"]), quote)
        message = str(payload.get("message") or "").strip()
        if not message:
            raise PreviewReviewError(
                "invalid_message", "A discussion message is required", 422
            )
        if mode == "revise" and not selected["editable"]:
            raise PreviewReviewError("source_revision_required", selected["reason"])
        # Authoritative contents are loaded on the server, never from browser context.
        contract = {
            "binding": ctx["binding"],
            "field": field,
            "current": selected["value"] if selected else None,
            "quote": quote,
            "mode": mode,
            "message": message,
            "fields": ctx["fields"],
            "read_only": ctx["read_only"],
        }
        from zf.runtime.workflow_dsl_prompt import PROFILE, workflow_dsl_prompt

        snapshot = hydrate_sidecar_ref(self.state_dir, ctx["proposal"]["effective_config_ref"]).payload
        contract["yaml"] = ctx["config_text"] if ctx["read_only"] else snapshot.get("yaml", "")
        contract["profile"] = PROFILE
        prompt = workflow_dsl_prompt(contract)
        turn = "preview-review-" + uuid.uuid4().hex
        source = self.writer.emit(
            "user.message",
            actor="web",
            payload={
                "source": "workflow-preview",
                "target": "kanban-agent",
                "runtime_delivery": "headless",
                "project_id": ctx["request"].get("project_id", ""),
                "conversation_id": f"workflow-preview:{ctx['request']['request_id']}",
                "message": message,
                "review": {
                    **{key: contract[key] for key in ("binding", "field", "quote", "mode", "profile")},
                    "effective_config_ref": ctx["proposal"]["effective_config_ref"],
                },
                "turn_id": turn,
            },
        )
        if agent is None:
            from zf.web.headless_agent import KanbanHeadlessAgent

            agent = KanbanHeadlessAgent(
                state_dir=self.state_dir, project_root=self.project_root
            )
        result = agent.run_turn(
            backend=str(payload.get("backend") or "codex-headless"),
            message=prompt,
            scope="project",
            thread_key=f"preview:{ctx['request']['request_id']}:{ctx['request']['revision']}",
            context={"turn_id": turn, "workflow_preview_review": contract},
            permission_profile="read_only",
        )
        if not result.ok or getattr(result, "incomplete", False):
            raise PreviewReviewError(
                "provider_failed", str(result.error or result.status), 502
            )
        try:
            reply = json.loads(result.reply)
        except (ValueError, TypeError) as exc:
            raise PreviewReviewError(
                "invalid_reply", "Agent must return a review JSON object", 422
            ) from exc
        if (
            not isinstance(reply, dict)
            or not isinstance(reply.get("answer"), str)
            or set(reply) - ({"answer", "changes"} if mode == "chat" else {"answer", "replacement"})
        ):
            raise PreviewReviewError("invalid_reply", "Unexpected review output", 422)
        latest = self.context(ctx["binding"], _allow_started=ctx["read_only"])
        if latest["config_digest"] != ctx["config_digest"]:
            raise PreviewReviewError(
                "stale_preview", "Config changed during discussion; refresh"
            )
        draft_ref = None
        changes = []
        patches = reply.get("changes", []) if mode == "chat" else (
            [{"field": field, "replacement": reply["replacement"]}]
            if mode == "revise" and "replacement" in reply else []
        )
        if ctx["read_only"] and patches:
            raise PreviewReviewError("snapshot_read_only", "Run snapshots cannot produce configuration patches")
        if not isinstance(patches, list) or len(patches) > len(ctx["fields"]):
            raise PreviewReviewError("invalid_patch", "Expected bounded field changes", 422)
        seen = set()
        for patch in patches:
            if not isinstance(patch, dict) or set(patch) != {"field", "replacement"}:
                raise PreviewReviewError("invalid_patch", "Expected field and replacement", 422)
            target = patch["field"]
            if not isinstance(target, str) or target in seen or target not in ctx["fields"]:
                raise PreviewReviewError("invalid_patch", "Unknown or repeated field", 422)
            seen.add(target)
            if quote and target != field:
                raise PreviewReviewError("invalid_patch", "Quoted edits must stay within the selected field", 422)
            before = ctx["fields"][target]["value"]
            after = patch["replacement"]
            if quote:
                original = _text(before)
                offset, end = _quote_range(original, quote)
                if isinstance(before, str) and isinstance(after, str):
                    after = before[:offset] + after + before[end:]
                elif offset != 0 or end != len(original):
                    raise PreviewReviewError("invalid_reference", "Non-text fields must be replaced as a whole", 422)
            if type(before) is not type(after) or (
                isinstance(after, str) and not after.strip() and not target.endswith(".model")
            ):
                raise PreviewReviewError("invalid_patch", "Replacement must preserve the field type and be nonempty", 422)
            if after != before:
                changes.append({"field": target, "before": before, "after": after})
        if changes:
            self._validate_changes(ctx, changes)
            draft_ref = write_immutable_json_sidecar(
                self.state_dir,
                {
                    "schema_version": SCHEMA,
                    "binding": ctx["binding"],
                    "config_digest": ctx["config_digest"],
                    "changes": changes,
                    "quote": quote,
                    "answer": reply["answer"],
                    "source_event_id": source.id,
                    "base_request": ctx["request"],
                    "base_proposal_ref": ctx["proposal_ref"],
                },
                root="workflow/preview-reviews", kind="workflow_preview_review",
                schema_version=SCHEMA, created_by="workflow-preview-review",
            )
        self.writer.emit(
            "kanban.agent.reply",
            actor="web",
            causation_id=source.id,
            payload={
                "source": "workflow-preview",
                "answer": reply["answer"],
                "turn_id": turn,
                "review_ref": draft_ref,
                "mutates_task_state": False,
            },
        )
        return {
            "ok": True,
            "answer": reply["answer"],
            "changes": changes,
            "draft_ref": draft_ref,
            "binding": ctx["binding"],
        }

    def _validate_changes(self, ctx: dict, changes: list) -> None:
        if not changes or len(changes) > len(ctx["fields"]):
            raise PreviewReviewError("invalid_patch", "Expected bounded field changes", 422)
        fields = [change.get("field", "") for change in changes]
        config_change = all(field.startswith("config.") for field in fields)
        if len(set(fields)) != len(fields) or (len(changes) > 1 and not config_change):
            raise PreviewReviewError("invalid_patch", "Use distinct config fields or one requirement field", 422)
        for change, field in zip(changes, fields):
            spec = ctx["fields"].get(field, {})
            if (
                not spec.get("editable")
                or spec.get("value") != change.get("before")
                or type(change.get("after")) is not type(spec.get("value"))
            ):
                raise PreviewReviewError("invalid_patch", "Patch does not match the current editable field", 422)
        if config_change:
            path = self._candidate_config(ctx, changes)
            try:
                compiled = load_config(path)
                if any(key in field for field in fields for key in (".roleSkillBundles.", ".role_skill_bundles.")) and (
                    {role.name: list(role.skills) for role in compiled.roles} == ctx["compiled_role_skills"]
                ):
                    raise PreviewReviewError("ineffective_skill_binding", "该 skills 输入没有改变实际角色绑定，请选择编译器支持的角色或技能组", 422)
                from zf.runtime.workflow_preflight import build_flow_preflight_report

                report = build_flow_preflight_report(
                    path,
                    flow_kind=self._flow_kind(ctx),
                    intake_path=Path(
                        str(
                            ctx["manifest"].get("intake_json_ref")
                            or ctx["manifest"].get("intake_ref")
                            or ""
                        )
                    ),
                    workflow_input_manifest_path=Path(
                        ctx["request"]["workflow_input_manifest_ref"]
                    ),
                    allow_missing_env=True,
                )
                if report.get("status") == "STOP" and ctx["proposal"].get("approval_status") != "blocked":
                    raise PreviewReviewError(
                        "config_preflight_blocked",
                        "Candidate config failed preflight; revise the selected field",
                        422,
                    )
            finally:
                path.unlink(missing_ok=True)

    def _candidate_config(self, ctx: dict, changes: list) -> Path:
        docs = list(yaml.safe_load_all(ctx["config_text"]))
        for change in changes:
            docs = apply_config_field(docs, change["field"], change["after"], roles=ctx.get("role_inputs"))
        # Beside zf.yaml so relative config references preserve their meaning.
        path = self.project_root / f".zf-preview-{uuid.uuid4().hex}.yaml"
        atomic_write_text(
            path,
            yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True)
            if changes
            else ctx["config_text"],
        )
        path.chmod(0o600)
        return path

    @staticmethod
    def _flow_kind(ctx: dict) -> str:
        return {"IssueFlow": "issue", "PrdFlow": "prd", "RefactorFlow": "refactor",
                "Workflow": "workflow"}.get(ctx["proposal"].get("flow_family"), ctx["request"]["kind"])

    def apply(self, descriptor: dict) -> dict:
        draft = hydrate_sidecar_ref(self.state_dir, descriptor).payload
        if not isinstance(draft, dict) or draft.get("schema_version") != SCHEMA:
            raise PreviewReviewError(
                "invalid_patch", "Not a Preview revision artifact", 422
            )
        digest = str(descriptor.get("sha256") or "")
        # The durable preparation consumer must not replay this intermediate
        # ready revision against the old canonical config while we compile it.
        with locked_path(self.state_dir / "projections" / "workflow-synthesis-consumer.guard"), \
                locked_path(self.state_dir / "locks" / "workflow-preview-mutation"):
            for event in reversed(self.writer.event_log.read_all()):
                if (
                    event.type == "web.action.completed"
                    and event.payload.get("preview_review_digest") == digest
                ):
                    return {**event.payload["result"], "replayed": True}
            live = load_workflow_request(self.state_dir, draft["binding"]["request_id"])
            resumed = any(
                event.type == "workflow.request.updated"
                and event.payload.get("source_event_id") == draft["source_event_id"]
                and event.payload.get("previous_revision")
                == draft["binding"]["revision"]
                for event in self.writer.event_log.read_all()
            )
            if resumed and (
                live.get("revision") != draft["binding"]["revision"] + 1
                or live.get("status") not in {"ready", "proposed"}
            ):
                raise PreviewReviewError(
                    "stale_preview", "Revision continued elsewhere; refresh"
                )
            ctx = self.context(
                draft["binding"], _request=draft["base_request"] if resumed else None,
                _proposal_ref=draft.get("base_proposal_ref") if resumed else None,
            )
            if ctx["config_digest"] != draft["config_digest"]:
                raise PreviewReviewError(
                    "stale_preview", "Config changed; request a fresh review"
                )
            changes = draft["changes"]
            self._validate_changes(ctx, changes)
            updates: dict[str, Any] = {}
            change = changes[0]
            config_change = change["field"].startswith("config.")
            if not config_change:
                _, key, *index = change["field"].split(".")
                value = deepcopy(ctx["requirement"][key])
                if index:
                    value[int(index[0])] = change["after"]
                else:
                    value = change["after"]
                updates[key] = value
            current = (
                live
                if resumed
                else revise_workflow_request(
                    self.state_dir,
                    Path(ctx["request"]["workflow_input_manifest_ref"]),
                    actor="web-owner",
                    expected_revision=ctx["binding"]["revision"],
                    expected_requirement_digest=ctx["request"][
                        "requirement_spec_digest"
                    ],
                    confirm=True,
                    source_event_id=draft["source_event_id"],
                    writer=self.writer,
                    **updates,
                )
            )
            candidate = self._candidate_config(ctx, changes if config_change else [])
            try:
                synthesis_ref = self._carry_synthesis(ctx, current, changes, candidate)
                current = load_workflow_request(self.state_dir, current["request_id"])
                # The compiler rechecks the candidate, builds immutable artifacts,
                # and binds a new proposal. No config apply or Run is performed.
                from zf.runtime.workflow_preflight import build_flow_preflight_report

                report = build_flow_preflight_report(
                    candidate,
                    flow_kind=self._flow_kind(ctx),
                    intake_path=Path(
                        str(
                            ctx["manifest"].get("intake_json_ref")
                            or ctx["manifest"].get("intake_ref")
                            or ""
                        )
                    ),
                    workflow_input_manifest_path=Path(
                        current["workflow_input_manifest_ref"]
                    ),
                    allow_missing_env=True,
                )
                report["resolved_pattern_id"] = (
                    ctx["proposal"].get("preflight", {}).get("resolved_pattern_id", "")
                )
                proposal, proposal_ref = build_workflow_proposal(
                    self.state_dir,
                    request=current,
                    base_config_path=self.config_path,
                    candidate_config_path=candidate,
                    preflight=report,
                    flow_kind=self._flow_kind(ctx),
                    synthesis_result_ref=synthesis_ref,
                    preview_review_ref=descriptor,
                    actor="web-owner",
                    writer=self.writer,
                )
            finally:
                if candidate != self.config_path:
                    candidate.unlink(missing_ok=True)
            result = {
                "ok": True,
                "status": "revised",
                "request_id": current["request_id"],
                "revision": current["revision"],
                "proposal_ref": proposal_ref,
                "proposal_digest": proposal["proposal_digest"],
                "blockers": proposal.get("blockers", []),
                "requires_config_apply": config_change,
                "started": False,
            }
            self.writer.emit(
                "web.action.completed",
                actor="web",
                payload={
                    "action": "workflow-preview-revise",
                    "preview_review_digest": digest,
                    "result": result,
                },
            )
            return result

    def _carry_synthesis(
        self, ctx: dict, current: dict, changes: list, candidate: Path
    ) -> dict | None:
        descriptor = ctx["request"].get("synthesis_ref")
        if not descriptor:
            return None
        prior = hydrate_sidecar_ref(self.state_dir, descriptor).payload
        spec = hydrate_sidecar_ref(self.state_dir, prior["short_flow_spec_ref"]).payload
        keys = (
            "schema_version",
            "selected_flow_family",
            "decision_rationale",
            "assumptions",
            "open_questions",
            "requested_roles",
            "requested_skills",
            "requested_profiles",
            "completion_profile",
            "risk_hints",
        )
        result = {key: deepcopy(prior[key]) for key in keys if key in prior}
        short_spec = {
            key: deepcopy(spec[key])
            for key in ("flow_family", "intent", "parameters", "purpose", "template")
            if key in spec
        }
        compiled = load_config(candidate)
        parameters = short_spec.setdefault("parameters", {})
        if "task_pipeline" in parameters and any(
            change["field"].split(".")[-1] in {"taskPipeline", "task_pipeline", "flowProfile", "flow_profile"}
            for change in changes
        ):
            kind = str(result["selected_flow_family"]).lower().removesuffix("flow")
            metadata = (getattr(compiled.workflow, "flow_metadata_by_kind", {}) or {}).get(kind)
            if not metadata:
                metadata = compiled.workflow.flow_metadata
            policy = metadata.get("task_pipeline") or {}
            if policy:
                parameters["task_pipeline"] = {
                    "profile": policy["profile_id"], "mode": policy["mode"],
                    "impl_capacity": policy["pools"]["impl"]["capacity"],
                    "verify_capacity": policy["pools"]["verify"]["capacity"],
                    "max_active_task_pipelines": policy["max_active_task_pipelines"],
                }
            else:
                parameters.pop("task_pipeline")
        for change in changes:
            if change["field"].startswith("config."):
                key = change["field"].split(".")[-1]
                if key in {"lanes", "strictness"} and not parameters.get("controller_id"):
                    short_spec.setdefault("parameters", {})[key] = change["after"]
                if key == "lanes":
                    # Lane expansion is compiler-owned. A smaller approved lane
                    # count must not retain references to removed lane instances.
                    family = str(result["selected_flow_family"]).lower().removesuffix("flow")
                    prior_roles = set(result.get("requested_roles", []))
                    result["requested_roles"] = sorted({role.name for role in compiled.roles
                        if role.flow_kind == family or (not role.flow_kind and role.name in prior_roles)})
        result.update(
            request_id=current["request_id"],
            request_revision=current["revision"],
            requirement_ref=current["requirement_spec_ref"],
            requirement_digest=current["requirement_spec_digest"],
            short_flow_spec=short_spec,
        )
        from zf.runtime.workflow_synthesis import run_workflow_synthesis

        outcome = run_workflow_synthesis(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=compiled,
            writer=self.writer,
            request_id=current["request_id"],
            actor="web-owner",
            candidate_result=result,
        )
        return outcome.result_ref
