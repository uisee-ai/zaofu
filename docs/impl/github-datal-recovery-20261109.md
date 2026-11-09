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
