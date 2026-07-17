---
paths:
  - "docs/**"
---

# Documentation Rules — docs/

This rule auto-loads only when working on documentation. Keeps main
CLAUDE.md focused on architecture.

## 子目录语义边界(2026-05-19 audit 后确定)

zaofu 的 `docs/` 是分类的,不是堆。每个新 doc 必须**明确归属一类**:

| 子目录 | 内容 | 状态特征 |
|---|---|---|
| `docs/design/` | **架构 / 决策 / 原则** — 长期有效的设计文档 | 单调递增数字前缀 `<number>-<slug>.md`;ADR 在 `adr/` |
| `docs/impl/` | **实施 walkthrough** — 描述具体落地路径、子任务拆解 | 通常配对一份 design doc(同主题)|
| `docs/ideas/` | **预设计候选**(candidate / idea / deferred) | 编号 `NNN-<slug>.md`(3 位)。不是真相,**未立项不 implement** |
| `docs/runbooks/` | **操作手册** — operator 日常任务步骤 | 无编号,文件名即语义 |
| `docs/manual/` | **用户手册** — CLI / Web / Feishu 使用说明 | 无编号 |
| `docs/refer/` 或 `zaofu-refer/` | **外部项目分析** — 借鉴参考(Trellis / AgentFlow / 等) | 不是 zaofu 自身设计,**只读** |
| `docs/records/` | **运行记录** — 一次性 audit / 诊断结果归档 | 时间戳前缀 |
| `docs/codex/` | codex backend 集成参考 | 受 codex 上游约束 |
| `docs/multica/` / `docs/ralph/` / `docs/new-design/` | 历史/借鉴材料 | 一般不新增 |

**写新 doc 前先问**:它该进哪一类?如果模糊 → 多半应该在 `docs/ideas/` 等先做完功课再升级。

## 编号纪律

`docs/design/` 使用单调递增的十进制数字前缀 `<number>-<slug>.md`；0..99
保留两位零填充，100 以后自然使用三位。`docs/impl/` 沿用其现有编号序列，
`docs/ideas/` 用 3 位 `NNN-<slug>.md`。

**禁止**:
- 重号(如 `docs/impl/21-foo.md` + `docs/impl/21-bar.md` —— 当前真有这种 cosmetic 债,
  见 `docs/impl/21-git-evidence-briefing-implementation.md` + `21-memory-store-wire-up.md`,
  `22-worker-lifecycle-*.md` + `22-zaofu-canonical-dag.md`)
- 跳号写未来(如直接写 `50-foo.md` 占位)
- 同号目录间共用(`design/42` 和 `impl/42` OK,但 `design/42-a.md` + `42-b.md` 不行)

**做法**:开新 doc 时读取 `docs/design/00-index.md` 的保留号说明，再用
`rg --files docs/design | sed -nE 's#docs/design/([0-9]+)-.*#\1#p' | sort -n | tail -1`
核实最大编号和空号；当前 117 是历史保留空号，不能复用。

## 00-index 注册强制

**新增 `docs/design/<number>-*.md` 必须**同步更新 `docs/design/00-index.md`:

1. 在当前文档路由表按编号位置加一行
2. 写一句话摘要 + doc 142 定义的状态(`canonical-current` / `implemented` /
   `partial` / `candidate` / `historical` / `superseded`)
3. 如果是借鉴 doc(`zaofu-refer/` 或 doc 42/43 这种),也要交叉引用源仓库路径

**反 pattern**:doc 36-41 创建后 **00-index 漏登 5 个 sprint**(2026-05-19 我才补 43/44),
导致 doc 39/40/41 的 master plan 在 index 里**不可发现**。

## 孤儿 doc 检测

每写完一个新 design / impl doc 之前 commit 前自检:

```bash
# 1. 是否被 00-index 引用
rg "<number>-<slug>" docs/design/00-index.md

# 2. 是否被任何 src/ / 其它 doc / backlog 引用
rg -l "<number>-<slug>" src docs backlogs tasks

# 3. 如果都为 0 → 孤儿 doc,不要 commit
```

孤儿 doc = library code without callers 的纸面版本,**累积成"幽灵设计"**(似乎设
计过但没人看)。

## 长度纪律

- design doc 单文件 **≤1000 行**。超过就拆 ADR(`docs/design/adr/ADR-NNNN-<slug>.md`)
- impl doc 单文件 **≤500 行**。它应该是"具体怎么做",不是"为什么这么做"
- 当前超 1000 的 design doc(doc 39 / 40 / 41)是 long-horizon master plan 这一
  例外,但**不要新增超长 doc**

## design vs impl vs ideas 升降级流向

```
idea (docs/ideas/NNN-)  ──候选验证──►  design (docs/design/<number>-)
                                         │
                                         ├──实施分解──►  impl (docs/impl/NN-)
                                         │
                                         └──写完┑
                                                ├──►  ADR (docs/design/adr/)
                                                ┘    (固化决策不变更)
```

- **idea → design**:经过审计,确认值得做时,从 `ideas/NNN-` 升级。**不是改名**,
  是新建 design doc + idea 文件加 "升级为 docs/design/<number>-..." 状态头
- **design → impl**:写完设计,开 sprint 实施时,产 impl doc
- **design → ADR**:决策固化(如 ADR-0001 三层架构),不再变更
- **idea → deferred**:idea 文件加 `状态: deferred` + 触发条件,留作历史

## hash 引用纪律(2026-06-11,三轮漂移教训)

**docs/ 正文禁钉 commit hash**。dev 分支多节点并行 + 高频 rebase 会让
钉死的 hash 反复腐烂(doc 87 §12 两枚 hash 一天内失效两次)。写"已入
git"、按标题/`git log --follow` 引用即可。例外:`tasks/` 归档按
backlogs.md 规则必须记 short hash——由
`tests/test_archive_hash_integrity.py` 机械守护(漂移时按标题回退
匹配并打印重映射表)。
