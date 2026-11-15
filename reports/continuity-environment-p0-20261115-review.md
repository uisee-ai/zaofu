# 环境 P0：共享依赖保护、实例回执与必需环境准入

> 状态：本增量实施与验收；B05/F11/F13 保持部分完成，不标整项 done。

基线 `497ee9cc`，分支 `wip/codex-20261114-continuity-handoff`。
用户授权参考 LoopForge 继续优化，并将 Claude 增强降优先级。
本轮改变的是 Provider 共用的环境控制路径，未恢复 Datal、未修改运行项目配置。

## 已接入的行为

1. 资源声明通过 atomic writer 保存，provision 来源和消费者均登记；锁绑定实际依赖目标。
   同一资源被不同 worktree 使用时，受控 setup/cleanup 不能各凭工作区私锁绕过。
2. 原生 Provider 继承多资源锁；stream-json 使用相同资源规则。旧启动计划遇到软链接
   重定向、目录实例替换、声明变化或损坏时拒绝执行。锁冲突释放已取得的锁，不无限等待。
3. setup marker 增加资源实例回执：脚本与输入摘要不变，但目标或 device/inode/mode
   改变时也不能直接复用。可重做 setup 恢复缺失环境，保留未提交成果。
4. `runtime.workdirs.required_paths` 明确声明必需输出，默认空列表。Task/Role 在准备后
   检查，实际 Task Pipeline 派发在缺失时不创建 Provider operation；Candidate 的环境
   failed 报告阻止质量检查。Python 自动 bootstrap 的失败残留不能满足必需 venv 资格。
5. 配置 loader/render 和 registered controller 保留新字段；没有将本仓库的语言依赖
   强加给所有目标项目。显式 setup 与版本/ABI/依赖内容验收仍由项目负责。

配置、路径和资格限制见 [实现说明](../docs/impl/continuity-environment-resources.md)。

## 测试过程与结果

- 第一轮 RED：共享依赖正在使用仍可被兄弟/源工作区安装器修改；缺必需环境仍可完成
  Task workspace 准备；新配置未消费。10 failed / 1 passed，原始日志保留。
- 实例 RED：替换依赖目录后仍直接复用旧 setup marker。加入资源实例回执后修复。
- 实际调用方覆盖：Task/Role preparation、Task Pipeline dispatcher、Candidate rebuild、
  native exec mock Provider、zf stop；并覆盖并行只读复用、声明扩展冲突、损坏声明、旧启动计划拒绝。
- 一项测试误用单文档 YAML 读取多文档配置，修复为读取 ZfConfig 文档，未修改业务断言。
- 既有 stream 测试直接 patch 全局 time.sleep，额外 Git 子进程的超时轮询也被统计进来；
  改为只替换 transport 模块引用的 clock，仍断言其会话等待恰好一次。
- 新的目录实例恢复能力使早期“删目录后必然报错”预期不再成立。改为验证声明的 setup
  恢复目录、未提交 candidate 保留；没有 setup/安装失败的阻断反例继续保留。
- 最终影响闭包、sentinel 和 Flow mock E2E 的退出码及数量见下方最终验收记录。
- planner 以 dev 为基线检查累计变更；宽全仓 deterministic/serial 层用明确影响闭包
  替代，Web 没有本轮改动，沿用前批验证。本轮自动执行 contract/mock_e2e；未执行全仓
  pytest、真实 Provider/宿主 tmux、Docker 多语言环境或 Datal 产品验收。

原始命令、日志、修正前失败、dev-verify receipt 与源码摘要保存于本地
`reports/continuity-environment-p0-20261115/`。历史多轮测试有重叠，不相加为独立测试总数。

## 未完成与后续

- 完整资源层级冲突、未声明外部目标、跨宿主资源、未经过受控入口的 shell 安装和孤儿工具隔离。
- Node/Python/Go 的真实版本/ABI/包内容资格及统一语言准备适配；本轮不取消 Python 工作区本地 venv。
- 准备到实际业务使用之间的持久租约闭环、环境的新建验收/原子切换/回滚、可恢复 cleanup 全阶段事务。
- B02 活跃输入修订、B04 独立监督与未知效果恢复，以及其他 Spec 尚未验收条款不由本轮替代。

没有 merge、push 或部署；工作成果继续保留在当前实施分支。

## 最终验收记录

- 影响闭包：474 passed（配置、环境、实际派发/清理调用方、Provider mock transport）。
- 独立会话锁合同：5 passed。
- sentinel：53 passed；Flow mock E2E：10 passed。
- 最终无测试失败；早期失败、fixture 修正与行为预期调整均保留在原始日志。
- 非 Git 资源锁改为宿主同 UID 私有目录，并验证获取锁不会创建缺失依赖目录；
  目录创建前后锁身份保持一致。真实语言工具、真实 Provider 和产品验收未执行。
