# PRD／SDD 方法与完整性声明验证

本批基于 ZaoFu `8f174350`，对照本地 Spec Kit `7370e592` 的默认方法，
完成现有三个 Skill 的补强和 Preview 声明修复。没有引入 Spec Kit 运行时，
没有新增 Task schema、业务语义评分器或全局设计 gate。
任务记录：`tasks/done/2026-10-31-1103-sdd-plan-readiness.md`。

## 实际增加的能力

1. 产品规格方法：按用户价值组织故事、优先级理由、独立验收和成功判据；
   引用已确认来源，区分工程支撑、业务 KPI 与编码义务。
2. 当前实施前沿的设计方法：数据真源、正式接口、事务/幂等、UI 状态与非功能
   要求；公共合同明确生产者/消费者及同版锚点，未来决定保留影响和关闭时点。
3. 独立 Critic 方法：产品、设计、来源覆盖、执行验证和未决治理分项给证据，
   明确全范围/有界返修及未评部分。
4. Preview 声明修正：缺失 profile 不推断为完整；显式完整/摘要保持兼容，
   正文仍可读，原审批控制不变。Graph 持久缓存与 ETag 更新代际。

前三项是 Agent 方法改进，不是 Runtime 自动保证语义质量。canonical `skills/`
与两个 provider 分发副本同步，真实角色物化覆盖新增引用。

## 机械验证

- `uv sync --extra dev --extra web`：完成隔离 worktree 依赖准备。
- 新 profile 回归在修复前 **5 failed / 7 passed**；修复后 profile、Graph
  缓存/API 和角色物化聚焦 **31 passed**。
- `uv run python scripts/dev-verify.py plan --base dev --workers 2
  --tests tests/e2e/test_kanban_plan_approve_mock_e2e.py`：选出 59 个 Python
  测试文件及前端 typecheck/unit；覆盖 caller、投影、批准入口和 mock E2E。
- 同参数 `run --keep-going`：**1116 passed / 1 skipped / 1 failed**；
  Web typecheck 与 unit 通过。不是全仓 pytest，也不是真实 provider E2E。
- 唯一 Python 失败为
  `tests/test_feishu_callback_security.py::test_plan_approve_button_by_approver_emits_plan_approved`。
  在未改动的 dev `8f174350` 独立运行，同样失败于第 228 行 `ok is True`；
  本批没有改飞书批准路径或降低该断言。整体 impact receipt 如实为 failed。
- `bash scripts/dev-premerge-gate.sh`：**51 passed**，manual docs currentness 正常。
- `npm --prefix web run build`：typecheck、构建与 bundle budget 通过。
- `uv run ruff check tests/test_plan_content_profile.py tests/test_plan_content_profile_cache.py`：
  通过。
- 项目 `read_skill_metadata(expected_name=...)` 校验 **9 份元数据通过**。
  skill-creator 的通用 quick_validate 不接受本仓已有的 stages/tags/dependencies
  等字段及既有 description 的箭头；这是校验器合同不兼容，未删除有效项目元数据
  来让通用校验变绿。

- Docker `mcp/playwright:latest`（镜像 `8342e03ef357`）、隔离 API
  `0.0.0.0:8013`：`plan-preview-content.spec.ts` 的 **7 项通过**；
  新 `plan-preview-profile.spec.ts` **1 项通过**，共覆盖 8 个用例。
  新用例通过真实 API/React 检查“需核对完整性”、正文可读及审批入口保持可用。
- 浏览器首次暴露测试夹具把 `inbox-item-read` 计作审批次数，trace 证明另一个
  请求才是唯一 `replan-approve`。修正后仅排除该已读动作，仍对所有其他动作
  计数并核对审批 identity。新用例的同名目录/正文定位改用 heading 后通过。
- 前端测试变化后单独重跑 `dev-verify.py run --paths` 对应三个 Web 测试/配置文件：
  typecheck 与 unit 通过。

## 独立前向评估

采用 skill-creator 的独立前向验证方法。评估者只得到新方法、ls 来源节选/冻结
任务及决定、合成会议室预约输入；没有提供本次预期答案或既有审查结论。
完整[前向评估产物](2026-10-31-sdd-method-forward-evaluation.md)保留实际规格、
反例和局限；临时输入、摘要及检查结果保存在
`/tmp/zf-sdd-evaluation-20261031-1103/`，均为评估证据而非运行态。
ls 输入从同版 Plan 的 task_map/execution_spec 提取 LS-SCAFFOLD、LS-OPERATIONS、
LS-WORK 及 Q01/Q02/Q04/Q09/Q11；原文只取 1958–1975、2586–2646 行，未复制凭据。
不把独立评估等同于正式 plan-critic 对在线 Plan 的准入 verdict。

评估者实际识别了 WORK 公共协议未确定、局部 probe 不能证明业务事务、
部分人工场景缺 AC/证据层级等问题，同时允许已授权脚手架继续；会议室样例
补回遗漏的本人取消，将并发唯一性从 READ 移交实际 BOOK 写入生产者，并保留
未来必需统计范围。没有发明性能指标或把未提供的完整图认定为已缺失。

首次方法评估发现 `blocking:false` 与消费者就绪、`covered` 与语义核对的
表述歧义。本批已作明确区分，评估者仅针对这两处补充独立复核，确认歧义解决，
案例不重跑、结论不改变。方法摘要：
`859480357f6c5b9282aebb44967e001861dfbacf30dcc63c60f25493915eb83b`
→ `ff1ba85ea9fb2956d4303ec9e2baea49c32aabced5ea67aae180c8ccdd112b45`。
随后角色物化/Skill provenance/instruction 回归 **42 passed / 1 skipped**。
最终文档/Skill impact 子集通过 **41 passed / 1 skipped**。
只证明两个有界案例和已测试机械路径，不认证所有未来 Plan 或 ls 产品完成。

## ls 受控修订交接

本次只读基线是 Plan `evt-3c805b837cb9`、run
`prepare-TASK-90D92C-e4eaededbd2b74fe`。原始
`lfms/loshu-fms-handoff.md` SHA-256：
`57e24bbe2574f21094b9da33cc5093f7f9cbfbaf174c5a47a48f10cc04575e85`。
实际发起修订时必须先核对当前 Plan 和已完成任务，不能假定这仍是最新执行版本。

受控修订输入：

- 原始范围不缩减；为核心旅程补用户目标、优先级理由、来源/AC 与成功判据，
  工程基础设施单列。没有来源支持的性能阈值保留待决。
- 优先核对 LS-OPERATIONS／LS-WORK 的公共事务、幂等和未知结果合同；Q02/Q04/Q09
  逐项明确 owner、消费时点和关闭证据。具体实现协议通过有权设计决定落实，
  不从本评估例子反向生成项目需求。
- 核对 SRC-0003、SRC-0004 的总览责任及 SRC-2214–2216 的安装结果责任。
  复用[已有来源纠偏记录](2026-10-31-ls-plan-coverage-review.md)，同时审相邻同类条款。
- 共享合同变化更新生产者/消费者分区、Task/AC/命令和真实依赖；保留未受影响且
  仍 current 的已完成工作。不得原地修改冻结工件或沿用旧版审批。
- 经独立 Critic 对序列化后的同版工件复审，再通过现有受控 replan/适用审批准入。
  先在一条真实前沿验证首个可运行反馈，再扩展其余切片；独立 Verify 与最终全量
  验收保留。

## 采用与限制

本批仅生成仓库实现、方法验证和上述修订交接，没有修改 ls 运行态或宣布其
现有 PRD 达标。代码尚需合入实际消费者版本并重启 Web/API；Planner/Critic
后续会话需重新物化 Skills。无需为了显示修正重启整个 workflow。
历史事件/Bundle 的显式声明仍保留，无法自动区分过去真实声明与旧兜底生成的值。
相关实现说明见[Plan 执行一致性](../impl/plan-execution-readiness.md)。
