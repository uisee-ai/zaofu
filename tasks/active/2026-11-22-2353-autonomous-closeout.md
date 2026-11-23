> 状态: active

# Research 建议实施：无需外部监工的恢复与交付

用户已批准实施 Kimi/Codex 调研建议；本轮验收目标是外部监工退出后，ZaoFu 仍能完成诊断、修复、独立验证和交付。保持原需求及质量标准，不把未知结果转成功。

基线：dev e178f43c；先快进纳入前轮已验证 1de62173（部署版本 0f04bf71），不修改旧冻结 worktree 或主目录脏文件。

## 已批准工作与验收

- [x] A1 mixed Candidate：OA 按证据 finding 分流，局部产品修复与未决义务并存 -> verify: 实际 OA/受控 amendment 入口、缺项/错摘要/越权拒绝、重启幂等、未决事项不能形成最终通过。
- [x] A2 修复派发失败收敛及自动续接：同一失败不逐 tick 重做，持久交给 RM 诊断 -> verify: 实际 RM pending/调度、事件重放与新授权尝试；已有工作区不盲删、不冒领。
- [x] A3 执行事实与消费身份分离：同源码及等价合同的重新冻结可复用事实 -> verify: 新 candidate 身份可复用；合同/源码/环境变化拒绝；不能生成语义通过票。
- [x] A4 方法与现有恢复接通：需求边界、增量复验、WRC/索引按需读取、技术失败和语义否决分流 -> verify: 复用现有入口回归与技能同步，不新增第二状态机。
- [x] A5 连续性与宽度：8 Impl + 8 Task Verify 不变，最终审核独立配置；无新证据不反复整轮审核 -> verify: 现有恢复/宽度/重放回归及补充集成检查。
- [ ] A6 原断点修复回归：保留原 Run/任务/账本，从受控断点恢复 -> verify: 部署加载证据、真实新派发/产物/最终交付；如再失败记录并修复，不宣称无人资格通过。

## 验证边界

执行聚焦及影响闭包、premerge 哨兵、确定性 mock E2E；记录 dev 全分支计划和相对已验证前轮基线的增量闭包，避免重复运行已验证历史变更。
旧无人窗口继续 closed_failed；修复期所有人工操作记录。修复回归通过后，独立窗口才验证零介入及原始需求完整交付。真实授权不足或外部服务不可用需明确请求，不伪造成功。

## 调研来源

Kimi e12cad1a：持久任务通知去重、执行结果分类、压缩后索引检索。Codex 44848822：持久根会话恢复、前序 turn 身份校验与原子续接、权限和执行环境匹配、技术输出失败有限重试。二者均不能直接证明任意业务长任务无人成功；本仓库须有实际调用方及验收证据。

## 实施补充与复核

- 独立补采：owner 精确命令 allowlist、容器不存在证明、锁内重新校验、原 start 最多一次，未知原事件不改写；测试包含拒绝未授权/容器存活/暂停/额度不足/查询失败/二次未知。
- 未决义务必须提供当前冻结输入 ref/sha256；合同原意解释不强制新审批，声称 owner 变更绑定真实批准请求/trigger/当前代际；新合同仍由原有准入路径控制。
- 新 effect 开始前保存完整 manifest；补采已知非零只是解决执行未知，产品失败仍保留。跨代当前同命令独立观测不能冒充旧 effect 成功。
- 同源事实复用同时检查真实依赖摘要和环境，不因 freeze 身份变化或采证容量提高重复执行全部命令。
- cdd-1:1 research 独立只读复核未发现剩余 P0/P1 部署阻断；要求旧 start 先补采再推进新 freeze，已纳入恢复顺序。

## 回归记录（实施中）

首次影响闭包：12128 passed、18 skipped、2 failed，503 秒。
两项失败已核实：历史配置测试样本应排除尚不存在的 recollect_commands；cost watchdog 测试固定在 11/21，跨日后被 runtime_event_window 正常过滤，前轮 1de62173 同样复现。仅修正测试时间与历史样本，不修改生产时间窗口或放宽策略。
后续相关聚焦 68 passed；义务真实提交路径 14 passed；最后完整影响闭包正在运行，原失败记录保留。
真实 Docker tier 2 passed；不等于真实 provider 最终交付。原无人资格继续 closed_failed。

原断点恢复、业务缺陷修复及最终交付尚未完成；A6 保持 active，不将本批补丁通过冒充无人资格通过。


最终验证：`scripts/dev-verify.py run --base 1de62173 --workers 4` -> passed。
影响闭包 12142 passed、18 skipped（482 秒）；哨兵 53 passed；流程 mock E2E 10 passed。
新未决义务实际 RM 调度另行聚焦 9 passed；真实 Docker 显式 tier 2 passed、10 deselected。
三份技能分发一致；新增 impl 文档已由既有恢复文档引用；git diff --check 通过。
相对 dev 的完整选择计划：400 文件、1471 个测试文件、10 域，无未映射错误。
执行层以已验证 1de62173 为增量基线；没有将历史 dev 全分支测试重复执行，也未声称 full pytest 或真实 provider 最终验收通过。
证据：reports/autonomous-closeout/verification-final.json、verification-final.log、host-final.log、obligation-dispatch-final.log。
原失败影响闭包 verification.json/verification.log 保留，未覆盖成通过。
