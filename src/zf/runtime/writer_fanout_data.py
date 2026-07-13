"""Writer/fanout 数据提取与约束评估 — K1 切片 4b(守 ≤500 再拆)。

verbatim;路径越权为纯评估(返回 violation 描述),裁决留宿主。"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.events.module_parity import is_module_parity_scan_completed_event
import subprocess


_FANOUT_AFFINITY_METADATA_KEYS = (
    "assignment_strategy",
    "lane_profile",
    "lane_id",
    "stage_slot",
    "affinity_tag",
    "pipeline_id",
    "root_fanout_id",
    "upstream_root_fanout_id",
    "upstream_fanout_id",
    "upstream_child_id",
    "upstream_task_id",
    "upstream_stage_slot",
)


class WriterFanoutDataMixin:
    @staticmethod
    def _first_nonempty(*values) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _writer_task_contract_list(value) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _fanout_config_value(source: object, key: str) -> str:
        if isinstance(source, dict):
            return str(source.get(key) or "")
        return str(getattr(source, key, "") or "")

    @classmethod
    def _fanout_child_result_events(cls, aggregate: object) -> tuple[str, str]:
        success = (
            cls._fanout_config_value(aggregate, "child_success_event")
            or "workflow.child.completed"
        )
        failure = (
            cls._fanout_config_value(aggregate, "child_failure_event")
            or "workflow.child.failed"
        )
        return success, failure

    def _writer_fanout_recovery_target(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        current_fanout_id: str,
    ) -> tuple[dict, dict] | None:
        if event.type != "dev.build.done":
            return None
        return self._writer_fanout_completion_target(
            event=event,
            payload=payload,
            current_fanout_id=current_fanout_id,
            statuses={"failed"},
        )

    @staticmethod
    def _is_plan_artifact_stage(
        *,
        role: RoleConfig,
        stage_id: str,
        success_event: str,
        child_success_event: str = "",
    ) -> bool:
        haystack = " ".join([
            stage_id,
            success_event,
            child_success_event,
            role.name,
            " ".join(str(stage) for stage in (role.stages or [])),
        ]).lower()
        if "review" in haystack and "plan" not in haystack:
            return False
        return (
            "plan" in haystack
            or "arch.proposal.done" in haystack
            or "task_map.ready" in haystack and "refactor" in haystack
        )

    @staticmethod
    def _plan_artifact_contract_lines() -> list[str]:
        return [
            "Plan stages must produce a durable markdown plan artifact, not transcript-only prose.",
            "Write the plan under `docs/plans/` or a stage-specific artifact directory before emitting the success event.",
            "Include `plan_artifact_ref` or `plan_ref` in the result payload, and include the same path in `artifact_refs`.",
            "When a task map is produced, include `task_map_ref`; when source coverage is available, include `source_index_ref`.",
            "If the plan splits work across more than one parallel bundle (distinct `owner_role` bundles running concurrently), it MUST include one separate task with `root_owner_class: \"assembly\"` that owns the shared entrypoint/wiring and merges the bundles. The writer fanout admission rejects a multi-bundle task_map lacking it and forces a replan — declare the assembly task up front.",
            "For refactor plan handoff, include `scan_quality_audit_ref` and list that audit artifact in `artifact_refs`.",
            "If the workflow asks for a full manifest, emit `artifact.manifest.published` with `kind=implementation_plan` and `kind=task_map` refs before the terminal success event.",
        ]

    @staticmethod
    def _collect_payload_list(payloads: list[dict], key: str) -> list[str]:
        values: list[str] = []
        for payload in payloads:
            raw = WriterFanoutDataMixin._payload_or_report_value(payload, key)
            if isinstance(raw, list):
                values.extend(str(item) for item in raw if str(item or ""))
            elif raw not in (None, ""):
                values.append(str(raw))
        return values

    @staticmethod
    def _first_child_value(
        manifest: dict,
        payloads: list[dict],
        key: str,
    ) -> str:
        for payload in payloads:
            value = WriterFanoutDataMixin._payload_or_report_value(payload, key)
            if value not in (None, ""):
                return str(value)
        value = manifest.get(key)
        return str(value) if value not in (None, "") else ""

    @staticmethod
    def _payload_or_report_value(payload: dict, key: str):
        value = payload.get(key)
        if value not in (None, ""):
            return value
        report = payload.get("report")
        if isinstance(report, dict):
            report_value = report.get(key)
            if report_value not in (None, ""):
                return report_value
        # P0-3 (2026-06-19 e2e): the child inherits the upstream stage's
        # contract fields (prd_ref / artifact_refs / evidence_refs from
        # prd.ready) via trigger_payload at dispatch, but a reader role does
        # not necessarily re-emit them in its result. When a required
        # success-payload field is absent from both the child top level and
        # its report, fall back to the inherited trigger_payload so the
        # aggregate satisfies required-schema (e.g. prd.approved requires
        # prd_ref) instead of failing the contract gate with the data sitting
        # one level down. Top-level / report still win — this is a last resort.
        # trigger_payload sits either directly on the value-dict or one level
        # down on the manifest child record's ``payload`` sub-dict (the shape
        # _fanout_child_payloads hands the aggregate); check both.
        trigger = payload.get("trigger_payload")
        if not isinstance(trigger, dict):
            inner = payload.get("payload")
            if isinstance(inner, dict):
                trigger = inner.get("trigger_payload")
        if isinstance(trigger, dict):
            return trigger.get(key)
        return None

    def _generic_fanout_success_payload(
        self,
        *,
        manifest: dict,
        success_event: str,
        extra_payloads: list[dict] | None = None,
    ) -> dict:
        """Project generic reader-fanout handoff fields from payload/report data."""
        child_payloads = self._fanout_child_payloads(manifest)
        payloads = [
            payload for payload in (extra_payloads or [])
            if isinstance(payload, dict)
        ] + child_payloads
        artifact_refs = self._collect_payload_list(payloads, "artifact_refs")
        evidence_refs = self._collect_payload_list(payloads, "evidence_refs")
        report_refs = self._collect_payload_list(payloads, "report_refs")
        inventory_refs = self._collect_payload_list(payloads, "inventory_refs")
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            for key in ("report_path", "result_path"):
                value = str(child.get(key) or "")
                if value:
                    report_refs.append(value)
        artifact_refs = self._dedupe_strings([*artifact_refs, *report_refs, *inventory_refs])
        evidence_refs = self._dedupe_strings([*evidence_refs, *inventory_refs])

        payload: dict = {
            "artifact_refs": artifact_refs,
            "evidence_refs": evidence_refs,
        }
        if inventory_refs:
            payload["inventory_refs"] = self._dedupe_strings(inventory_refs)
        # E3-2(审计 D3 dead-end 修复):quality-floor 词表键必须随聚合
        # 透传,否则 judge children 报了 repro/demo/e2e 证据、聚合
        # judge.passed 却把键丢掉,_reject_flow_judge_evidence_gap 永拒
        # —— Issue/PRD 终局在结构上不可能过自己的 floor 门。
        for list_key in (
            "regression_refs", "test_refs", "demo_refs",
            "e2e_refs", "parity_refs", "provider_refs",
        ):
            values = self._collect_payload_list(payloads, list_key)
            if values:
                payload[list_key] = self._dedupe_strings(values)
                evidence_refs.extend(values)
        repro_ref = self._first_child_value(manifest, payloads, "repro_ref")
        if repro_ref:
            payload["repro_ref"] = repro_ref
            evidence_refs.append(repro_ref)
        payload["evidence_refs"] = self._dedupe_strings(evidence_refs)
        plan_artifact_ref = (
            self._first_child_value(manifest, payloads, "plan_artifact_ref")
            or self._first_child_value(manifest, payloads, "plan_ref")
        )
        backlog_ref = self._first_child_value(manifest, payloads, "backlog_ref")
        source_index_ref = self._first_child_value(
            manifest,
            payloads,
            "source_index_ref",
        )
        source_inventory_ref = (
            self._first_child_value(manifest, payloads, "source_inventory_ref")
            or self._first_child_value(
                manifest,
                payloads,
                "hermes_source_inventory_ref",
            )
        )
        inventory_scalar_refs = {
            "inventory_ref": self._first_child_value(
                manifest,
                payloads,
                "inventory_ref",
            ),
            "source_inventory_ref": source_inventory_ref,
            "inventory_coverage_matrix_ref": self._first_child_value(
                manifest,
                payloads,
                "inventory_coverage_matrix_ref",
            ),
            "expected_module_parity_report_paths_ref": self._first_child_value(
                manifest,
                payloads,
                "expected_module_parity_report_paths_ref",
            ),
        }
        if plan_artifact_ref:
            payload["plan_artifact_ref"] = plan_artifact_ref
            artifact_refs.append(plan_artifact_ref)
        if backlog_ref:
            payload["backlog_ref"] = backlog_ref
            artifact_refs.append(backlog_ref)
        if source_index_ref:
            payload["source_index_ref"] = source_index_ref
            evidence_refs.append(source_index_ref)
        for key, value in inventory_scalar_refs.items():
            if value:
                payload[key] = value
                artifact_refs.append(value)
                evidence_refs.append(value)
        if plan_artifact_ref or backlog_ref or source_index_ref or any(inventory_scalar_refs.values()):
            payload["artifact_refs"] = self._dedupe_strings(artifact_refs)
            payload["evidence_refs"] = self._dedupe_strings(evidence_refs)

        first_child_event_id = self._first_child_value(
            manifest,
            payloads,
            "result_event_id",
        ) or self._first_child_value(manifest, payloads, "last_event_id")
        if "design" in success_event:
            payload["critic_event_id"] = first_child_event_id
        for key in ("prd_ref", "spec_ref", "research_ref"):
            value = self._first_child_value(manifest, payloads, key)
            if value:
                payload[key] = value
                artifact_refs.append(value)
        if any(payload.get(key) for key in ("prd_ref", "spec_ref", "research_ref")):
            payload["artifact_refs"] = self._dedupe_strings(artifact_refs)

        if success_event == "task_map.ready":
            task_map_ref = (
                self._first_child_value(manifest, payloads, "task_map_ref")
                or str(manifest.get("task_map_ref") or "")
            )
            source_commit = (
                self._first_child_value(manifest, payloads, "source_commit")
                or self._git_commit(str(manifest.get("target_ref") or "HEAD"))
            )
            base_commit = (
                self._first_child_value(
                    manifest,
                    payloads,
                    "candidate_base_commit",
                )
                or source_commit
            )
            pdd_id = (
                self._first_child_value(manifest, payloads, "pdd_id")
                or str(manifest.get("pdd_id") or manifest.get("feature_id") or "")
            )
            feature_id = (
                self._first_child_value(manifest, payloads, "feature_id")
                or str(manifest.get("feature_id") or pdd_id)
            )
            payload.update({
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "task_map_ref": task_map_ref,
                "source_commit": source_commit,
                "candidate_base_commit": base_commit,
            })
        if success_event == "flow.discovery.completed":
            trigger_payload = (
                manifest.get("trigger_payload")
                if isinstance(manifest.get("trigger_payload"), dict)
                else {}
            )
            gap_tasks: list[dict] = []
            for child_payload in payloads:
                for source in (child_payload, child_payload.get("report")):
                    if not isinstance(source, dict):
                        continue
                    raw_tasks = source.get("gap_tasks") or source.get("tasks")
                    if isinstance(raw_tasks, list):
                        gap_tasks.extend(
                            task for task in raw_tasks
                            if isinstance(task, dict)
                        )
            if gap_tasks:
                payload["gap_tasks"] = gap_tasks
                payload["gap_task_count"] = len(gap_tasks)
                payload.setdefault("open_p0_p1_gap_count", len(gap_tasks))
            for key in (
                "pdd_id",
                "feature_id",
                "goal_id",
                "goal_kind",
                "flow_kind",
                "gap_category",
                "discovery_profile",
                "trace_id",
                "task_map_ref",
                "source_index_ref",
                "source_commit",
                "candidate_base_commit",
                "candidate_ref",
                "target_ref",
                "gap_plan_ref",
                "open_p0_p1_gap_count",
                "open_gap_count",
                "parity_status",
                "closure_status",
                "goal_status",
            ):
                value = (
                    self._first_child_value(manifest, payloads, key)
                    or trigger_payload.get(key)
                    or manifest.get(key)
                )
                if value not in (None, ""):
                    payload[key] = value
            for key in ("affected_task_ids", "supersedes_task_ids"):
                values = self._collect_payload_list(payloads, key)
                if not values:
                    raw = trigger_payload.get(key)
                    if isinstance(raw, list):
                        values = [str(item) for item in raw if str(item).strip()]
                if values:
                    payload[key] = self._dedupe_strings(values)
        if is_module_parity_scan_completed_event(success_event):
            from zf.runtime.module_parity_gap_synthesis import (
                filter_open_p0_p1_gap_tasks,
                synthesize_gap_tasks_from_parity_payloads,
            )

            trigger_payload = (
                manifest.get("trigger_payload")
                if isinstance(manifest.get("trigger_payload"), dict)
                else {}
            )
            gap_tasks: list[dict] = []
            for child_payload in payloads:
                for source in (child_payload, child_payload.get("report")):
                    if not isinstance(source, dict):
                        continue
                    raw_tasks = source.get("gap_tasks") or source.get("tasks")
                    if isinstance(raw_tasks, list):
                        gap_tasks.extend(
                            task for task in raw_tasks
                            if isinstance(task, dict)
                        )
            gap_tasks = filter_open_p0_p1_gap_tasks(gap_tasks)
            if gap_tasks:
                payload["gap_tasks"] = gap_tasks
                payload["gap_task_count"] = len(gap_tasks)
                payload.setdefault("open_p0_p1_gap_count", len(gap_tasks))
            for key in (
                "pdd_id",
                "feature_id",
                "trace_id",
                "task_map_ref",
                "source_index_ref",
                "source_commit",
                "candidate_base_commit",
                "candidate_ref",
                "target_ref",
            ):
                value = (
                    self._first_child_value(manifest, payloads, key)
                    or str(trigger_payload.get(key) or "")
                    or str(manifest.get(key) or "")
                )
                if value:
                    payload[key] = value
            for key in (
                "gap_plan_ref",
                "open_p0_p1_gap_count",
                "parity_status",
                "module_id",
            ):
                value = self._first_child_value(manifest, payloads, key)
                if value:
                    payload[key] = value
            if not gap_tasks:
                synthesis = synthesize_gap_tasks_from_parity_payloads(
                    payloads,
                    pdd_id=str(payload.get("pdd_id") or ""),
                    source_index_ref=str(payload.get("source_index_ref") or ""),
                    evidence_refs=self._payload_string_list(
                        payload.get("evidence_refs")
                    ),
                )
                if synthesis.gap_tasks:
                    payload["gap_tasks"] = synthesis.gap_tasks
                    payload["gap_task_count"] = len(synthesis.gap_tasks)
                if "open_p0_p1_gap_count" not in payload:
                    payload["open_p0_p1_gap_count"] = (
                        synthesis.open_p0_p1_gap_count
                    )
                if synthesis.open_findings:
                    payload["open_p0_p1_findings"] = synthesis.open_findings
            elif "open_p0_p1_gap_count" not in payload:
                payload["open_p0_p1_gap_count"] = len(gap_tasks)
        payload = self._relocate_reader_fanout_artifact_refs(
            payload=payload,
            payload_sources=payloads,
            manifest=manifest,
        )
        return payload

    def _relocate_reader_fanout_artifact_refs(
        self,
        *,
        payload: dict,
        payload_sources: list[dict],
        manifest: dict,
    ) -> dict:
        try:
            from zf.runtime.fanout_artifact_refs import (
                relocate_fanout_artifact_refs,
            )

            return relocate_fanout_artifact_refs(
                payload=payload,
                payload_sources=payload_sources,
                manifest=manifest,
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
                roles=list(getattr(self.config, "roles", []) or []),
            )
        except Exception:
            return payload

    @staticmethod
    def _success_payload_contract_failure(
        success_event: str,
        payload: dict,
    ) -> str:
        if success_event == "task_map.ready" and not str(
            payload.get("task_map_ref") or ""
        ).strip():
            return "task_map.ready requires task_map_ref"
        if success_event in {"prd.ready", "prd.approved"}:
            prd_ref = str(payload.get("prd_ref") or "").strip()
            if not prd_ref:
                return f"{success_event} requires prd_ref"
            artifact_refs = WriterFanoutDataMixin._payload_string_list(
                payload.get("artifact_refs")
            )
            if prd_ref not in artifact_refs:
                return f"{success_event} requires artifact_refs including prd_ref"
            evidence_refs = WriterFanoutDataMixin._payload_string_list(
                payload.get("evidence_refs")
            )
            if not evidence_refs:
                return f"{success_event} requires evidence_refs"
        return ""

    @staticmethod
    def _payload_string_list(value) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item or "").strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _contract_failure_payload(payload: dict, reason: str) -> dict:
        enriched = dict(payload)
        enriched.setdefault("artifact_refs", [])
        enriched.setdefault("evidence_refs", [])
        enriched["contract_gate"] = "failed"
        enriched["reason"] = reason
        diagnostics = enriched.get("diagnostics")
        if isinstance(diagnostics, list):
            enriched["diagnostics"] = [*diagnostics, reason]
        else:
            enriched["diagnostics"] = [reason]
        return enriched

    @staticmethod
    def _dedupe_strings(values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out

    def _git_commit(self, ref: str) -> str:
        ref = ref or "HEAD"
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def _writer_affinity_task_item(
        self,
        stage,
        task_item: dict,
        *,
        lane_id: str = "",
        role_instance: str = "",
    ) -> dict:
        assignment = getattr(stage, "assignment", None)
        profile_id = str(getattr(assignment, "lane_profile", "") or "")
        stage_slot = str(getattr(assignment, "stage_slot", "") or "impl")
        affinity_key = self._fanout_affinity_key(stage)
        copied = dict(task_item)
        payload = (
            dict(copied.get("payload"))
            if isinstance(copied.get("payload"), dict)
            else {}
        )
        copied["payload"] = payload
        raw_affinity_tag = copied.get(affinity_key)
        if raw_affinity_tag in (None, ""):
            raw_affinity_tag = payload.get(affinity_key)
        raw_task = copied.get("raw_task")
        if raw_affinity_tag in (None, "") and isinstance(raw_task, dict):
            raw_affinity_tag = raw_task.get(affinity_key)
        affinity_tag = str(raw_affinity_tag or "").strip()
        if not affinity_tag:
            # prod-e2e(2026-07-04 prd 轮实弹):planner 按 task-map 合同交付
            # 但合同从未要求 affinity_tag,单任务 task_map 因此整盘取消。
            # 确定性回退:task_id 即亲和键(每任务独占 lane,impl→verify
            # 同 lane 语义成立);显式 tag 仍优先。
            affinity_tag = str(copied.get("task_id") or "").strip()
        if not affinity_tag:
            raise RuntimeError(
                f"writer fanout affinity key {affinity_key!r} missing "
                f"for task {copied.get('task_id')!r} and task_id fallback empty"
            )
        copied.update({
            "assignment_strategy": "affinity_stage_slots",
            "lane_profile": profile_id,
            "stage_slot": stage_slot,
            "affinity_tag": affinity_tag,
        })
        if lane_id:
            copied["lane_id"] = lane_id
        if role_instance:
            copied["role_instance"] = role_instance
        return copied

    @staticmethod
    def _copy_fanout_assignment_metadata(target: dict, source: dict) -> None:
        for key in _FANOUT_AFFINITY_METADATA_KEYS:
            value = source.get(key)
            if value not in (None, ""):
                target[key] = value

    @staticmethod
    def _fanout_child_identity_diagnostics(context) -> list[dict[str, object]]:
        from zf.runtime.affinity_review_scope import affinity_scope_identity_errors

        diagnostics: list[dict[str, object]] = []
        for child in getattr(context, "expected_children", []) or []:
            payload = child.payload if isinstance(child.payload, dict) else {}
            errors = affinity_scope_identity_errors(
                payload,
                role_instance=str(getattr(child, "role_instance", "") or ""),
            )
            if errors:
                diagnostics.append({
                    "child_id": str(getattr(child, "child_id", "") or ""),
                    "role_instance": str(getattr(child, "role_instance", "") or ""),
                    "lane_id": str(payload.get("lane_id") or ""),
                    "stage_slot": str(payload.get("stage_slot") or ""),
                    "errors": errors,
                })
        return diagnostics

    @staticmethod
    def _writer_task_items(data: object) -> list[dict]:
        from zf.runtime.writer_fanout_admission import writer_task_items

        return writer_task_items(data)

    def _lane_pipeline_for_trigger(self, trigger: str):
        """G3:trigger 匹配的 lane_pipeline spec(无则 None)。

        admission 期内容校验(assembly/根 owner,doc 90 §3.3.1)只对
        lane_pipeline 声明的扇出生效——手写 stages 行为不变。
        """
        for spec in getattr(self.config.workflow, "pipelines", []) or []:
            if getattr(spec, "trigger", "") == trigger:
                return spec
        return None

    @staticmethod
    def _validate_writer_task_items(task_items: list[dict]) -> None:
        from zf.runtime.writer_fanout_admission import validate_writer_task_items

        validate_writer_task_items(task_items)

    def _fanout_output_path_violation(self, fanout_id: str, payload: dict) -> str:
        from zf.core.safety import PathGuard, PathGuardError

        allowed_root = self.state_dir / "fanouts" / fanout_id
        for key in ("report_path", "output_path", "artifact_path"):
            value = str(payload.get(key) or "")
            if not value:
                continue
            path = Path(value)
            if not path.is_absolute():
                path = self.project_root / path
            try:
                PathGuard.assert_under(path, allowed_root)
            except PathGuardError:
                return f"fanout output path outside allowed root: {value}"
        return ""

    @staticmethod
    def _writer_protected_write(payload: dict) -> str:
        files = payload.get("files_touched")
        if not isinstance(files, list):
            return ""
        for raw_path in files:
            path = str(raw_path).strip()
            if path == ".zf" or path.startswith(".zf/"):
                return path
        return ""
