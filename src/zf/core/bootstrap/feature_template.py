"""Feature template for F-zaofu-bootstrap.

The description is rendered into `Feature.description` (visible in Web UI
and `zf kanban show`) AND mirrored to ``.zf/bootstrap.md`` so users can
read it in their editor.
"""

from __future__ import annotations

BOOTSTRAP_FEATURE_ID = "F-zaofu-bootstrap"
BOOTSTRAP_FEATURE_TITLE = "ZaoFu 冷启动引导 / First-run bootstrap"

BOOTSTRAP_FEATURE_DESCRIPTION = """# F-zaofu-bootstrap

欢迎来到 ZaoFu! 本 feature 是 `zf init` 自动创建的引导任务集,会带你跑一遍
multi-agent 工作流,让你不必先读完所有设计文档。

## 这一步:你已经完成

- ✅ `zf init` 已建好 `.zf/` 状态目录
- ✅ `zf.yaml` 已存在(或从 `--preset` 模板生成)
- ✅ 本引导 feature 已写入 `feature_list.json`
- ✅ 4 个引导 task (T-zfb-01 ~ T-zfb-04) 已写入 `kanban.json`

## 下一步:跑 `zf start`

```bash
zf start
```

orchestrator 会接管 `kanban.json` 里 backlog 状态的 task,逐个 dispatch 给
对应 role 的 worker(看 `zf.yaml` 的 `roles` 段决定派给谁)。

## 引导 task 概览

1. **T-zfb-01 Cold-start verify** — orchestrator 把第一个 task 路由给一个
   reader-role(arch / review / test 之一),让它跑 `zf validate --cold-start`
   并 emit `arch.proposal.done` 或同语义事件。
2. **T-zfb-02 First event flow** — dev role 接 `task.assigned` →
   写一个 demo 文件到 workdir → emit `dev.build.done`。
3. **T-zfb-03 Review chain** — review 接 `dev.build.done` 或
   `static_gate.passed`,把 demo emit `review.approved`,触发后续 test/judge。
4. **T-zfb-04 Customize CLAUDE.md** — 用户手动操作:打开 `CLAUDE.md` 添加
   自己项目的约束。本 task 是 manual,完成后跑 `zf kanban move T-zfb-04 done`
   收尾。

## 完成后

跑一次 `zf kanban --board` 应该看到 4 个 task 全部 done。再开始你自己的需求。

## 跳过本引导

- `zf init --skip-bootstrap` 在首次 init 时跳过(已 init 完则无效)
- 已经 init 但想清掉:`zf kanban move T-zfb-* cancelled` + 在 `feature_list.json`
  里改 F-zaofu-bootstrap.status 为 cancelled
"""

__all__ = [
    "BOOTSTRAP_FEATURE_ID",
    "BOOTSTRAP_FEATURE_TITLE",
    "BOOTSTRAP_FEATURE_DESCRIPTION",
]
