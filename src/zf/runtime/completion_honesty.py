"""完成事件诚实核验(r6-F7,r4 幻觉提交家族)。

r6 实弹:dev-scene 两次发完成事件而声称产物不存在(一次合同修正 3 分钟
后即"完成",一次以"selectors 存在"指代文件存在)——每次虚报烧掉一整轮
review。kernel 只核**声称与实物的一致性**(boundary:evidence 存在性
属 kernel;产物质量仍归 review/verify agent):

- 声称的 evidence/artifact 相对路径必须真实存在(workdir 或 project);
- 声称的 head_commit 必须等于 workdir 分支实头。

观测级(与 P3-3 同哲学):失败发 dev.completion.claims_unverified
(attention on_single),不阻塞——实测一轮后再议收紧为 fail-closed。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

_SKIP_PREFIXES = ("git:", "http://", "https://", "event:", "task:", "artifact:")


def _claimed_paths(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("evidence_refs", "artifact_refs"):
        raw = payload.get(key)
        if not isinstance(raw, list):
            report = payload.get("report")
            raw = report.get(key) if isinstance(report, dict) else None
        if not isinstance(raw, list):
            continue
        for item in raw:
            text = str(item or "").strip()
            if not text or text.startswith(_SKIP_PREFIXES):
                continue
            if text.startswith("/") or text.startswith(".zf"):
                continue  # 绝对路径/运行时投影不在本核验范围
            if "://" in text:
                continue
            out.append(text)
    return list(dict.fromkeys(out))


def unverified_completion_claims(
    payload: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> list[str]:
    """返回不可证实的声称清单(空 = 无声称或全部可证实)。"""
    problems: list[str] = []
    workdir_raw = str(payload.get("workdir") or "").strip()
    roots = []
    if workdir_raw:
        roots.append(Path(workdir_raw))
    if project_root is not None:
        roots.append(Path(project_root))
    if roots:
        for ref in _claimed_paths(payload):
            if not any((root / ref).exists() for root in roots):
                problems.append(f"claimed artifact missing on disk: {ref}")
    head_claim = str(
        payload.get("head_commit")
        or payload.get("candidate_head_commit")
        or ""
    ).strip()
    if head_claim and workdir_raw:
        try:
            actual = subprocess.run(
                ["git", "-C", workdir_raw, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            if actual and not actual.startswith(head_claim) and not head_claim.startswith(actual):
                problems.append(
                    f"claimed head_commit {head_claim[:12]} != workdir HEAD {actual[:12]}"
                )
        except Exception:
            pass
    return problems


__all__ = ["unverified_completion_claims"]
