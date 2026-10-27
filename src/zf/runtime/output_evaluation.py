"""Read-only, staged evaluation of a frozen text output.

The agent owns semantic criteria and findings. This module owns input identity,
stage ordering, immutable evidence, and the fail-closed result envelope.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.web.headless_agent import CodexHeadlessBackend, HeadlessBackend


SCHEMA = "output-evaluation.v1"
KINDS = {"response_output", "prd_output", "markdown_output", "prompt_design"}
MAX_INPUT_CHARS = 600_000
MAX_REPLY_CHARS = 600_000


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvaluationInput:
    kind: str
    objective: str
    source_text: str
    target_text: str
    decision_text: str = ""
    evidence_text: str = ""
    scope: str = "full"
    risk_focus: tuple[str, ...] = ()
    source_ref: str = ""
    target_ref: str = ""
    target_revision: int = 0
    context: dict[str, str] | None = None

    def manifest(self) -> dict[str, Any]:
        if self.kind not in KINDS or self.scope not in {"full", "focused"}:
            raise ValueError("unsupported evaluation kind or scope")
        if not self.objective.strip() or not self.target_text.strip():
            raise ValueError("objective and complete target text are required")
        if any(len(value) > MAX_INPUT_CHARS for value in (
            self.source_text, self.target_text, self.decision_text, self.evidence_text,
        )):
            raise ValueError("evaluation input exceeds the bounded text contract")
        return {
            "schema_version": SCHEMA,
            "kind": self.kind,
            "objective": self.objective,
            "scope": self.scope,
            "risk_focus": list(self.risk_focus),
            "source": {"ref": self.source_ref, "sha256": digest_text(self.source_text),
                       "present": bool(self.source_text.strip()), "chars": len(self.source_text)},
            "decisions": {"sha256": digest_text(self.decision_text), "chars": len(self.decision_text)},
            "evidence": {"sha256": digest_text(self.evidence_text), "chars": len(self.evidence_text)},
            "target": {"ref": self.target_ref, "sha256": digest_text(self.target_text),
                       "revision": self.target_revision, "chars": len(self.target_text)},
            "context": dict(self.context or {}),
            "method_version": ("channel-full-test-and-prd-evaluation-prompts.v3"
                               if self.kind == "prd_output" and (self.context or {}).get("channel_id")
                               else "channel-full-test-and-prd-evaluation-prompts.v2"),
        }


def _json_reply(reply: str) -> dict[str, Any] | None:
    try:
        value = json.loads(reply.strip())
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _valid_matrix(report: dict[str, Any] | None, inventory: dict[str, Any], decisions: dict[str, Any] | None = None) -> bool:
    if not isinstance(report, dict) or not isinstance(report.get("matrix"), list):
        return False
    rows = report["matrix"]
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            return False
        criterion_id = row.get("criterion_id")
        result = row.get("result")
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            return False
        if result not in {"PASS", "FAIL", "INDETERMINATE"}:
            return False
        if result != "INDETERMINATE" and not str(row.get("target_location") or "").strip():
            return False
        ids.append(criterion_id)
    source_ids = {row["criterion_id"] for row in inventory["criteria"]}
    source_ids.update(str(row["criterion_id"]) for row in (decisions or {}).get("decision_deltas", [])
                      if isinstance(row, dict) and isinstance(row.get("criterion_id"), str) and row["criterion_id"].strip())
    return len(ids) == len(set(ids)) and source_ids.issubset(ids)


def _persist_stage_trace(state_dir: Path, base: str, stage: str, turn: Any,
                         *, actor: str, event_id: str) -> dict[str, Any]:
    reply = str(turn.reply or "")
    return write_sidecar_json(
        state_dir, f"{base}/{stage}-provider-result.json",
        {"stage": stage, "ok": turn.ok, "status": turn.status,
         "incomplete": turn.incomplete, "error": str(turn.error or "")[:1000],
         "completion_reason": str(turn.completion_reason or ""),
         "provider_session_id": turn.provider_session_id,
         "reply": reply[:MAX_REPLY_CHARS], "reply_truncated": len(reply) > MAX_REPLY_CHARS,
         "usage": turn.usage},
        kind="evaluation_provider_result", schema_version=SCHEMA,
        created_by=actor, source_event_id=event_id, required=True)


def _stage_prompt(stage: str, item: EvaluationInput, manifest: dict[str, Any], *,
                  inventory: dict[str, Any] | None = None,
                  decisions: dict[str, Any] | None = None) -> str:
    visible_manifest = dict(manifest)
    if stage != "target":
        visible_manifest["target"] = {key: value for key, value in manifest["target"].items()
                                      if key != "ref"}
    identity = json.dumps(visible_manifest, ensure_ascii=False, sort_keys=True)
    channel_prd = item.kind == "prd_output" and bool((item.context or {}).get("channel_id"))
    if stage == "inventory":
        return (
            "你是与作者、群内成员和已有 reviewer 隔离的只读评估者。现在只能建立评估标准，"
            "不得读取或推测目标产物。请先逐段读取全部来源；输出单个 JSON 对象，字段 "
            "criteria 为原子标准数组（criterion_id、source_location、expected、importance、"
            "verification），read_scope 和 unread_scope 都必须是字符串数组，"
            "只列原始 SOURCE 中已读/未读范围；尚未提供的决定与目标不能列入 unread_scope。"
            "rubric 为本轮 PASS/FAIL/"
            "INDETERMINATE 规则。不得从作者覆盖声明或洛书实例继承标准。没有来源时"
            " criteria 必须为空，rubric 仅限内部一致性、清晰度和可验收性，来源覆盖"
            "不可判定。此阶段尚未提供 Owner 决定或目标产物。"
            "若 kind=response_output，只把提问、明确输出约束以及直接影响回答正确性的"
            "参考规则列为必需标准；阅读参考资料不等于要求回答逐条复述整份资料。"
            "事实核对的简短答复不必展开未被询问的相关动作，除非省略会改变结论。"
            "不要使用 Markdown 代码围栏。\n"
            f"MANIFEST:\n{identity}\nSOURCE:\n{item.source_text}"
        )
    if stage == "decisions":
        conversation_guidance = (
            "Owner 消息和附件是未经预分类的聊天输入：结合上下文识别补充需求、更正、有效授权、"
            "未决问题、候选草案和平台操作指令；不得把候选草案或要求 Agent 执行操作本身"
            "当作产品义务。新增需求须分配稳定的 criterion_id 并记录消息或附件来源；"
            "后续有效更正须关联原标准，保留冲突和时序。criterion_id 仅用于需要在目标"
            "矩阵核对的产品要求，非产品操作或无效决定不要分配。"
        ) if channel_prd else ""
        return (
            "现在审查 Owner 输入、决定及授权证据，不得读取或推测目标产物。"
            "输出单个 JSON 对象：decision_deltas 为新增/修改/废止要求的逐项数组，"
            "每项须写明授权者、来源、作用范围、影响的 criterion_id、有效性；"
            "read_scope 和 unread_scope 必须分别是已读/未读决定证据的字符串数组，"
            "尚未提供的目标不能列入 unread_scope。" + conversation_guidance +
            "普通答复、"
            "测试假设、风险接受和未决项不得当成已授权产品变更。无决定时返回空数组。"
            "不要使用 Markdown 代码围栏。\n"
            f"MANIFEST:\n{identity}\nFROZEN_SOURCE_INVENTORY:\n"
            f"{json.dumps(inventory or {}, ensure_ascii=False)}\n"
            f"DECISION_EVIDENCE:\n{item.decision_text}"
        )
    return (
        "现在才提供目标产物。你必须按已冻结的标准逐项评估，且不能改写清单。"
        "输出单个 JSON 对象：verdict=PASS/FAIL/INDETERMINATE，matrix 为逐项结论数组，"
        "每行必须含 criterion_id、result=PASS/FAIL/INDETERMINATE、target_location、reason；"
        "逐项覆盖全部冻结 criterion_id，不能改名或遗漏；"
        + ("还须逐项覆盖决定阶段新增的需求 criterion_id；" if channel_prd else "") +
        "findings 为含严重度及来源/目标定位的数组，coverage 为实际检查与未查范围，"
        "score 为可选的辅助分数，product_verification 为通过/失败/未执行/阻塞。"
        "缺来源的 PRD/Markdown 只能判内部结构，整体来源覆盖为 INDETERMINATE；"
        "prompt_design 缺少运行案例与基线不得声称效果通过；回答缺事实证据不得声称"
        "事实正确。回答类 FAIL 必须有明确违反提问或关键约束的证据；未被询问的背景"
        "细节遗漏只作建议，缺少可核实证据的事实项标为 INDETERMINATE。"
        "事实证据未提供不等于事实不存在；缺证不能冒充反证。同版 canonical 合同、"
        "摘要绑定的 readiness 和事件证据应与正文一起核对。成员签字仅证明评审发生，"
        "不得据此推导内容质量通过；证据矛盾须定位说明，无法核实则 INDETERMINATE。"
        "若目标同时包含 Markdown 与结构化交接合同，须检查二者是否矛盾、结构化验收与"
        "验证命令能否覆盖来源要求；将计划在实施时创建的测试文件与声称已存在的文件区分。"
        "不得修改目标、确认 Channel、创建 Task 或启动 Workflow。"
        "不要使用 Markdown 代码围栏。\n"
        f"MANIFEST:\n{identity}\nFROZEN_SOURCE_INVENTORY:\n{json.dumps(inventory or {}, ensure_ascii=False)}\n"
        f"FROZEN_DECISION_DELTAS:\n{json.dumps(decisions or {}, ensure_ascii=False)}\n"
        f"TARGET:\n{item.target_text}\nOTHER_FACT_EVIDENCE:\n{item.evidence_text}"
    )


def evaluate_output(
    *,
    state_dir: Path,
    writer: EventWriter,
    item: EvaluationInput,
    request_id: str,
    actor: str = "output-evaluator",
    backend: HeadlessBackend | None = None,
    timeout_s: float = 1800,
    causation_id: str = "",
) -> dict[str, Any]:
    """Run two fresh-context turns; persist stage one before revealing target."""
    manifest = item.manifest()
    channel_prd = item.kind == "prd_output" and bool((item.context or {}).get("channel_id"))
    backend = backend or CodexHeadlessBackend()
    safe_id = str(uuid.UUID(request_id))
    base = f"artifacts/output-evaluations/{safe_id}"
    thread_id = str(uuid.uuid4())
    session_id = ""
    correlation_id = (item.context or {}).get("channel_id") or safe_id
    started = writer.emit("output.evaluation.started", actor=actor,
                          causation_id=causation_id or None, correlation_id=correlation_id,
                          payload={"request_id": safe_id, "manifest": manifest,
                                   "backend": backend.backend_id, "thread_id": thread_id,
                                   "permission_profile": "read_only"})
    input_ref = write_sidecar_json(
        state_dir, f"{base}/input-bundle.json",
        {"manifest": manifest, "source_text": item.source_text,
         "decision_text": item.decision_text, "target_text": item.target_text,
         "evidence_text": item.evidence_text},
        kind="evaluation_input_bundle", schema_version=SCHEMA,
        created_by=actor, source_event_id=started.id, required=True)
    manifest_ref = write_sidecar_json(state_dir, f"{base}/input-manifest.json", manifest,
                                      kind="evaluation_input_manifest", schema_version=SCHEMA,
                                      created_by=actor, source_event_id=started.id, required=True)
    result: dict[str, Any] = {"request_id": safe_id, "manifest_ref": manifest_ref["ref"],
                              "manifest_digest": manifest_ref["sha256"],
                              "input_ref": input_ref["ref"], "input_digest": input_ref["sha256"],
                              "verdict": "INDETERMINATE", "stage_traces": {}}
    workdir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if not backend.available():
            raise RuntimeError(f"{backend.backend_id} unavailable")
        def pin(value: str) -> None:
            nonlocal session_id
            session_id = value
        workdir = tempfile.TemporaryDirectory(prefix="zf-output-evaluation-")
        cwd = Path(workdir.name)

        def run_stage(stage: str, prompt: str, provider_session_id: str,
                      valid: Any) -> tuple[dict[str, Any], Any]:
            for attempt in (0, 1):
                turn = backend.run_turn(
                    prompt=prompt, cwd=cwd,
                    system_prompt="Independent, read-only evaluator. Return complete JSON only.",
                    thread_id=thread_id, provider_session_id=provider_session_id,
                    on_session_id=pin, on_message=None, timeout_s=timeout_s,
                    permission_profile="read_only",
                )
                trace_name = stage if attempt == 0 else f"{stage}-repair"
                trace = _persist_stage_trace(state_dir, base, trace_name, turn,
                                             actor=actor, event_id=started.id)
                result["stage_traces"][trace_name] = {"ref": trace["ref"],
                                                        "digest": trace["sha256"]}
                parsed = _json_reply(turn.reply)
                if turn.ok and not turn.incomplete and len(turn.reply) <= MAX_REPLY_CHARS and valid(parsed):
                    return parsed, turn
                if not turn.ok or turn.incomplete or len(turn.reply) > MAX_REPLY_CHARS:
                    break
                provider_session_id = session_id or turn.provider_session_id
                prompt = (
                    "上一条回复未满足本阶段 JSON 合同。只能修复格式或补齐本阶段字段，"
                    "不得读取下一阶段或新增来源。重发完整单个 JSON 对象。"
                    "read_scope/unread_scope 必须是字符串数组；未提供的下一阶段"
                    "材料不能算作本阶段未读范围。上一条回复：\n" + turn.reply
                )
            raise ValueError(f"{stage} result missing, malformed, or incomplete")

        inventory, inventory_turn = run_stage(
            "source", _stage_prompt("inventory", item, manifest), "",
            lambda value: isinstance(value, dict)
            and isinstance(value.get("criteria"), list)
            and all(isinstance(row, dict)
                    and isinstance(row.get("criterion_id"), str)
                    and row["criterion_id"].strip()
                    for row in value["criteria"])
            and len({row["criterion_id"] for row in value["criteria"]}) == len(value["criteria"])
            and isinstance(value.get("read_scope"), list)
            and isinstance(value.get("unread_scope"), list)
            and isinstance(value.get("rubric"), (dict, list)),
        )
        if item.source_text.strip() and not inventory["criteria"] and item.scope == "full":
            raise ValueError("full review produced no source criteria")
        if not item.source_text.strip() and inventory["criteria"]:
            raise ValueError("source-free review invented source criteria")
        if inventory["unread_scope"]:
            raise ValueError("source inventory left original source unread")
        inventory_ref = write_sidecar_json(
            state_dir, f"{base}/source-inventory.json", inventory,
            kind="evaluation_source_inventory", schema_version=SCHEMA,
            created_by=actor, source_event_id=started.id, required=True)
        writer.emit("output.evaluation.inventory.frozen", actor=actor,
                    causation_id=started.id, correlation_id=correlation_id,
                    payload={"request_id": safe_id, "ref": inventory_ref["ref"],
                             "digest": inventory_ref["sha256"], "thread_id": thread_id,
                             "provider_session_id": session_id})
        decisions, decision_turn = run_stage(
            "decisions", _stage_prompt("decisions", item, manifest, inventory=inventory),
            session_id or inventory_turn.provider_session_id,
            lambda value: isinstance(value, dict)
            and isinstance(value.get("decision_deltas"), list)
            and (not channel_prd or all(isinstance(row, dict)
                 and isinstance(row.get("criterion_id"), str) and row["criterion_id"].strip()
                 for row in value["decision_deltas"]))
            and isinstance(value.get("read_scope"), list)
            and isinstance(value.get("unread_scope"), list),
        )
        decision_ref = write_sidecar_json(
            state_dir, f"{base}/decision-delta-inventory.json", decisions,
            kind="evaluation_decision_inventory", schema_version=SCHEMA,
            created_by=actor, source_event_id=started.id, required=True)
        writer.emit("output.evaluation.decisions.frozen", actor=actor,
                    causation_id=started.id, correlation_id=correlation_id,
                    payload={"request_id": safe_id, "ref": decision_ref["ref"],
                             "digest": decision_ref["sha256"], "thread_id": thread_id,
                             "provider_session_id": session_id})
        report, report_turn = run_stage(
            "target", _stage_prompt("target", item, manifest, inventory=inventory, decisions=decisions),
            session_id or decision_turn.provider_session_id,
            lambda value: isinstance(value, dict)
            and value.get("verdict") in {"PASS", "FAIL", "INDETERMINATE"}
            and _valid_matrix(value, inventory, decisions if channel_prd else None)
            and isinstance(value.get("findings"), list)
            and isinstance(value.get("coverage"), (dict, list))
            and value.get("product_verification") in {"通过", "失败", "未执行", "阻塞"},
        )
        verdict = str(report["verdict"])
        matrix_results = {row["result"] for row in report["matrix"]}
        if "FAIL" in matrix_results:
            verdict = "FAIL"
        elif "INDETERMINATE" in matrix_results and verdict == "PASS":
            verdict = "INDETERMINATE"
        if item.kind in {"prd_output", "markdown_output"} and not item.source_text.strip():
            verdict = "INDETERMINATE"
        if item.kind == "prompt_design" and not item.evidence_text.strip():
            verdict = "INDETERMINATE"
        if inventory.get("unread_scope") and item.scope == "full":
            verdict = "INDETERMINATE"
        if decisions.get("unread_scope"):
            verdict = "INDETERMINATE"
        report["verdict"] = verdict
        report["request_id"] = safe_id
        report["manifest_ref"] = manifest_ref["ref"]
        report["manifest_digest"] = manifest_ref["sha256"]
        report["inventory_ref"] = inventory_ref["ref"]
        report["inventory_digest"] = inventory_ref["sha256"]
        report["decision_inventory_ref"] = decision_ref["ref"]
        report["decision_inventory_digest"] = decision_ref["sha256"]
        report["provider_session_id"] = session_id or report_turn.provider_session_id
        report["backend"] = backend.backend_id
        report_ref = write_sidecar_json(
            state_dir, f"{base}/report.json", report,
            kind="evaluation_report", schema_version=SCHEMA,
            created_by=actor, source_event_id=started.id, required=True)
        result.update(verdict=verdict, inventory_ref=inventory_ref["ref"],
                      inventory_digest=inventory_ref["sha256"],
                      decision_inventory_ref=decision_ref["ref"],
                      decision_inventory_digest=decision_ref["sha256"],
                      report_ref=report_ref["ref"], report_digest=report_ref["sha256"],
                      provider_session_id=report["provider_session_id"])
        terminal = "output.evaluation.completed"
    except Exception as exc:
        result["reason"] = str(exc)[:500]
        terminal = "output.evaluation.failed"
    finally:
        if workdir is not None:
            workdir.cleanup()
    writer.emit(terminal, actor=actor, causation_id=started.id,
                correlation_id=correlation_id, payload={**result, "manifest": manifest})
    return result
