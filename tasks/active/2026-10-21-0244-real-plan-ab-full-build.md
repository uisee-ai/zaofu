# 真实 Plan A/B 与完整产品 build 验证

> 状态: active

用户批准：2026-10-21 要求在新 worktree 执行通用改进，真实 A/B 和完整产品构建验证。
ZaoFu worktree 基于 eafd941d；比较基线 5191c9e6 与候选 eafd941d。

## 验收

- [x] A. 固定输入、模型、环境和评审任务，冻结两组完整 skills 与依赖，运行真实 provider。step -> verify: 原始轨迹、提交、输入摘要、token、耗时、完整/超时状态及写入检查可复查；漏包运行不得计入效果。
- [x] B. 实际项目启用候选方法。step -> verify: 比较旧项目 skill 与基线，保留定制；更新新产品 worktree，检查新 dispatch 的 materialization/provenance，不能只看 harness commit。
- [x] C. 独立完整产品目标经过 Scan/Planner/Critic，形成可审批 Plan。step -> verify: 根任务允许 app 实现、最终验收保留完整来源，审批前不派发产品 writer。`evt-88d5d35b562f` Critic 通过；`evt-477684e8999c` 为待批 Plan。
- [ ] D. 批准后从干净 worktree 安装、完整 build、真实入口、独立 Verify、Docker 浏览器及全部 mandatory 验收。step -> verify: 对应实际产品 commit 和运行收据；缺少数据/设备/业务决定仍未完成，不用文档或小样例替代。

- [x] E. 修正真实运行暴露的结果补正/旧 turn close 竞争。step -> verify: 实际失败事件链、有效 RED/GREEN、reader/synth caller 回归、身份错配和后续真实丢失保持可恢复；不放宽 read ledger 或业务门禁。
- [x] F. 移除有 Source Manifest 绑定的 synth briefing 中重复 control-result 正文。step -> verify: 完整正文及摘要/必读报告保留、legacy 回退保留；实际 Plan 隔离回放与直接 caller/读取凭证回归通过。模型效果另以真实 A/B 收据判断，不能由字节数推导。
- [x] G. 审批列表与预览使用相同的受限工件路径解析。step -> verify: 自定义 state dir、项目内旧引用、项目外路径拒绝三类回归；GET 不修改账本。29 项 API/CLI/Inbox/action 定向测试通过。

- [ ] H. 继续验证相对改善。step -> verify: 固定候选、质量 oracle 和成本判据；真实 cargo 三轮配对，加跨业务缺陷/合法对照；所有负结果保留，未达预注册标准不宣称更好。详见 `reading-eval/registration.json`。

## 已知边界

真实外部数据/GT/训练脚本/板端/仿真资源待 owner 提供位置；Plan 人工审批继续有效。
任务 TASK-D9818E，workflow wfint-prd-20261021024927036060，项目 full-build-20261021-c2f1cf22。
产品 worktree `/home/min/workspace/cargo-worktrees/full-build-20261021`。
证据根 `/home/min/workspace/public/zaofu-evidence/20261021-plan-ab`。

详见 [评估与构建记录](../../docs/impl/real-plan-ab-full-build.md)。

A/B 结果：两组独立的真实配对实验均完整运行；执行时序判断更准确、重复 briefing 体积减少，但两组耗时与输入均增加，整体优越性未证实。见关联记录，不把 A/C 完成写成 D 完成。

实施提交：`a8ed2d15`（评估工具）、`9a765e31`（补正交接）、`bc1593b1`（briefing 去重）、`3e665b54`（审批路径）。仅本地分支，未合并或推送。
本轮 [Plan Preview](http://10.9.100.143:8003/?project=full-build-20261021-c2f1cf22&page=inbox&plan=evt-477684e8999c) 待人工批准。候选 dashboard 与新产品 runtime 已加载修复；8001 与原 cargo 保持原会话。
