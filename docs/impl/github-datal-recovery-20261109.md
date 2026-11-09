# GitHub dev 合并与 datal 断点恢复（2026-11-09）

本轮由监工 session 执行用户授权：拉取 GitHub dev、整合本地更新，再恢复 datal；不推送远端。

## 来源与保全

- 原 dev：`43477f42`；GitHub 拉取目标：`052e6a40`。
- 恢复分支：`e62065db`，119 个本地提交；运行目录另外 181 个源码、测试、技能和文档路径保存为 `3fe3a300`。
- 独立整合目录：`zaofu-worktrees/github-datal-20261109`；产品运行态、候选分支、工件和 newloshu 目录未纳入源码提交。
- 本地分支合并后的模块恢复提交为 `e158d36c`。合并过程曾产生文件尾部缺失，哨兵捕获导入错误；修正后继续验证，未部署错误版本。

## 合并决策与验证问题

1. GitHub 的有界必读分页及输出交付证据契约替换旧版全量 JSONL/shared-bodies 缓存入口。保留每来源鉴权、摘要校验、完整分页与模型理解的区别，Planner/Verify 技能同步新协议；旧实现保留在原分支历史。
2. 保留显式批准的一次额外 Impl 重试，与新的语义失败计数规则共同生效；批准仅绑定下一代，不能重复绕过预算。
3. 保留已批准增量任务及前置任务选择，并与新版恢复替换归属校验组合；显式任务选择不自动扩大。
4. Run 级候选冻结同时检查当前任务代际、全部集成收据、已结束会话和候选提交；保留历史不完整冻结的取消逻辑。
5. Inbox 的人工审批历史要求真实审批请求；无请求的计划仍可由只读 Preview 查询，不能伪造人工审批历史。
6. 测试修正：去除固定日期的轮转窗口；签名错误保留 malformed 证据；冻结测试构造真实代际准入；浏览器测试按新 Graph 入口继续验证跨代替换及拒绝异 Run/修订/摘要/倒退事件。

## datal 恢复前断点

- Run：`workflow-0ea133e63454375e`；有效任务 50 个，36 done，14 未完成。
- `P11A` 实现结果已接纳，提交 `21a67cd056f9aaa054e947e09cd3d800787b1a40`；独立 Verify 尚未执行。
- `P16B-CONSUMER-WAITS-R1` Verify 因租约过期 suspended；目标 `9504a88ba9b102ce849304e387c62334dd3f6490`，已有未接纳报告保留。
- Plan Critic 同一 operation 重试时，动态 usage_observations 改变请求摘要，触发 request_hash_divergence，随后重规划次数耗尽并进入 Run blocked。
- 后续必须使用受控新代际/恢复入口，不能改写终态事件、直接编辑任务状态或绕过独立 Verify。

## 验证记录

合并验证和浏览器证据路径、恢复操作事件及后续监工问题在本文件续记。当前测试仅验证 harness 合并，不代表 datal 产品任务已经通过。

### 合并验证结果

- 后端影响闭包：876 passed、1 skipped（含确定性 mock E2E）；额外恢复计数/审批门禁闭包 91 passed。
- dev pre-merge sentinel：53 passed。前端 TypeScript、Vite 构建及 bundle budget 通过。
- Docker Chromium 149.0.7827.22；镜像 `sha256:8342e03ef3571d09afffcd00558b8067e66489a54e7f3f0d96a71a327fd45fe2`。
- 浏览器使用项目 Playwright 1.59.1 和镜像内显式 Chromium 路径；首次测试容器 UID 无目录写权限，改为宿主 UID 1001 并配置可写 HOME/cache 后执行原业务断言。
- 最终 Graph 浏览器证据保存在整合 worktree 的 `web/test-results-merge-final/`（trace/WebM）。
- 最终 Docker Graph 浏览器：22 passed（49.6s）；保留原断言、视频和 trace。

### 恢复实测与第二批修复

`27d7c10b` 已整合 GitHub 和本地分支。首次恢复的受控请求为
`evt-48570fe7e884`，新 Plan 触发为 `evt-ff9e66dab589`；P11A 的独立
Verify 已派发（`evt-6ccf145a1f2e`），不重复 Impl。

实测暴露以下恢复问题：

- Consumer Verify 旧请求 `c7f47128…` 与新请求 `d2a13de3…` 不一致。
  从真实不可变请求和当前 read-policy 重建，唯一差异是 required_reads：
  345 条变为 287 条。合同两个字段改为全文读取、同正文 planning_result/task_map
  去重、增加关键上下文；目标仍为 `9504a88b…`，合同和其他语义字段均未变化。
  新兼容证明只接受相同内容摘要覆盖原全部路径、无截断且读取要求不减弱；
  保留原请求摘要，侧车绑定当前完整请求和逐条覆盖证明。目标、合同、代次、
  内容摘要变化或丢失读取仍 fail-closed，不把 required_reads 从身份中一概剔除。
- 恢复扫描选择了 11 月 7 日的 `task_map.ready evt-3dace824bf49`，忽略
  后来的普通 Task Map 与人工授权的新规划代次。旧包 admission 失败
  `evt-5d261c07f19e` 又把新 Run 置为 blocked，并取消刚启动的 Planner。
  扫描现在按新 Task Map 和 Kernel 授权代次退休旧 rework，其他 Run 不受影响。
  不改历史事件或原审批，不重新启动已完成任务。
- 扩大到完整 Task Pipeline 相关测试发现原合并版本 5 个失败：两项预算错误
  文案不一致、两项“未派发修复”预算抵扣被重复前置门禁挡住、一项旧测试仍要求
  显式 semantic_triage_required 同时进入通用诊断。移除重复门禁，保留当前政策、
  抵扣和精确下一代授权；最后一项按现行单一 triage owner 契约更新验证，未放宽运行规则。
- Critic 空闲恢复会话残留旧额度错误，已通过新会话清除；这不等于新额度不足。
- 产品五个已部署 skill 的旧共享正文参数更新为当前有界分页指引，预映像留存。
  已派发上下文不重写；P11A 自行检测旧参数不可用并继续分页。

回归先红后绿：读取兼容测试最初 1 failed/9 passed，旧 Plan 重放测试最初
2 failed/1 passed。最终针对 Task Pipeline、workflow operation、candidate rework、
调用结果、恢复与三项 mock E2E 的闭包 666 passed；额外授权测试 28 passed；
流程 smoke 10 passed，sentinel 53 passed。未运行全量 pytest，不将 harness
测试视为 datal 产品验收。真实只读证明保存在产品目录
`diagnostics/github-dev-resume-20261109/checkpoint-compatibility-proof.json`。
