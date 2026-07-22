"""完成事件诚实核验(r6-F7,r4 幻觉提交家族)。

r6 实弹:dev-scene 两次发完成事件而声称产物不存在(一次合同修正 3 分钟
后即"完成",一次以"selectors 存在"指代文件存在)——每次虚报烧掉一整轮
review。kernel 只核**声称与实物的一致性**(boundary:evidence 存在性
属 kernel;产物质量仍归 review/verify agent):

- 声称的 evidence/artifact 相对路径必须真实存在(workdir、project 或
  configured runtime state dir);
- 声称的 head_commit 必须等于 workdir 分支实头。

观测级(与 P3-3 同哲学):失败发 dev.completion.claims_unverified
(attention on_single),不阻塞——实测一轮后再议收紧为 fail-closed。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

# evidence/artifact ref 约定:`<scheme>:<值>` 是**结构化引用**(git:/branch:/
# base:/task_map:/test:/cmd:/tag:/event:/http: ...),裸相对路径才是磁盘文件。
# 本门只核裸路径的存在性;凡带 scheme 前缀的引用一律不当磁盘路径查盘。
# 2026-07-08 教训:硬编码 scheme 白名单是打地鼠(branch: 修完 base:/task_map:/
# test: 又漏,live 轮实测三连假阳性),改用前缀模式一次覆盖现存与未来 scheme。
# 语法按 RFC 3986 scheme(ALPHA *(ALPHA/DIGIT/"+"/"-"/"."))+ 下划线——
# agent 拼写本身不稳定(同轮见 task_map: 与 task-map: 两种)。
# 裸路径无 `word:` 前缀(README.md / app/src/x.py),故不会被误跳。
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+._-]*:")


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
            if not text or _SCHEME_RE.match(text):
                continue  # 结构化引用(带 scheme 前缀),非磁盘路径
            if text.startswith("/") or text.startswith(".zf"):
                continue  # 绝对路径/运行时投影不在本核验范围
            out.append(text)
    return list(dict.fromkeys(out))


def unverified_completion_claims(
    payload: dict[str, Any],
    *,
    project_root: Path | None = None,
    state_dir: Path | None = None,
) -> list[str]:
    """返回不可证实的声称清单(空 = 无声称或全部可证实)。"""
    problems: list[str] = []
    workdir_raw = str(payload.get("workdir") or "").strip()
    roots = []
    if workdir_raw:
        roots.append(Path(workdir_raw))
    if project_root is not None:
        roots.append(Path(project_root))
    if state_dir is not None:
        roots.append(Path(state_dir))
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
