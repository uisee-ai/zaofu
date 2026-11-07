# R54：部分实现恢复时保留已集成依赖

> 状态: done

实现：a449bc7b `fix: include completed dependencies in partial recovery baselines`；dev 合并 ed3ee76f。

授权：用户持续监工、分析修复阻塞并记录。基于 dev 7e5a0641，新 worktree 实施。

## 现场复现

车辆端口03在 evt-62e594ed7e26 完成，Candidate dd4515b2 包含新 rtk-ports.mjs；RTK consumer03 由 evt-78f2865f53f7 自动派发，但 base=0db0fb0 不含该依赖。task_partial_successor 只证明旧 partial 合法并返回旧提交；旧测试只验证最终集成保留无关 Candidate，未验证 Impl 当前能消费新依赖。

## 验收

1. 新 successor workspace 组装完整基线 -> verify: 真实 Git 场景同时具备已核验 partial 与最新 Candidate 的依赖文件，保留两方祖先；不移动 Candidate、TaskRef 或原 worker ref。
2. 身份、范围和冲突守卫 -> verify: 原 failure/contract/run/ref/范围证明仍必需；冲突 fail closed，不做语义合并决策；重复计算稳定。
3. 在途工作区冻结 -> verify: 新派发使用新组装；已存在 workspace 的 Impl/Verify 不因后续 Candidate 前进被改写。
4. 交付闭包 -> verify: worker 从组合基线形成新delta，独立 Verify 和 Candidate 集成可正常消费；保留旧 partial 和并发依赖，无越界diff或重复发布。聚焦影响闭包、sentinel、mock E2E。
5. 现场恢复与记录 -> verify: 不直接改当前 active Task workspace/contract；待冻结尝试安全结束，经受控恢复/增量交付使用正确新基线，再验证实际文件和运行身份。记录 OA/RM 未发现原因及部署版本。不 push。

## 验证与待完成项

- 初次回归 RED：3 failed / 10 passed，准确复现缺依赖、未拒绝冲突与组合基线未交付。新增连续恢复测试曾因误用 Task.model_copy 产生6项测试夹具失败，已改用 deepcopy。
- 最终 `dev-verify.py run --base dev`：184 passed（16个受影响测试文件，含 eight-slots mock E2E）。
- TaskWorkspace、partial continuation、generic workflow mock E2E补充：22 passed；sentinel：53 passed。没有全量 pytest 或真实 Provider E2E。曾指定不存在的测试文件导致一次未收集测试，已纠正路径后完成上述22项。
- 真实 LS-v1 只读基线预演：5fa7e3404cc5576a06e9247346f8a4b73c956a54 同时保留 dd4515b2 Candidate、0db0fb0 partial 两方祖先，车辆端口与RTK目录均存在；未改在途工作区或任何分支。
- 新增同 Run、同源提交、显式单前任链的连续恢复证明；拒绝循环、歧义、基线变化和原始准入缺失。
- 尚未完成：部署后当前在途 consumer03 的安全停止和正式后继准入，以及新工作区实际生效记录。当前任务保持 active，不以预演替代现场恢复。

## 现场验收完成（20:14 UTC补录；真实派发20:00 UTC）

- consumer03真实阻塞准入evt-1d8b95149fa4后，经ControlledActionService发布04增量，ready evt-8c48082b16d7；仅批准此ready，统计owner待决保持。
- consumer04于20:00:12.642184由evt-0656da263b6b自动派发，op wop-impl-tp-FMS-08-GAP-CONSUME-04-73a9570-c0305ec671e9，attempt ta-88e4ed1e1ae902968677。
- 实际workspace base=2a00d59bf7c64fc7a259de98b508a6b0ac60122e，parents=696a2c9b151c182c60aa7fc38fba142bbe5f7a89 + 0db0fb094c6d34d62fb01d4cb2c810c0f122ed40。车辆端口、端口合同、原RTK目录均存在；旧工作区HEAD与干净状态不变。
- 新输入dc67799c4e50508c55394e1604232b34ca5ec1cf90aa109759fd0a2680b874a8，完整shared和34个分区body保持，其他任务输入绑定不变。运行时PID3988344加载ed3ee76f，原worker身份/进程保留。
- 现场证据：LS-v1 docs/records/2026-11-07-r54-live-verification.json、2026-11-07-r54-controlled-reload.json、2026-11-07-r54-rtk-recovery-result.json。
- 监工曾漏查新operation/pane，误报等待OA。精确事件已更正：OA是shadow，不阻塞，派发在审批后117.697秒发生。监工采样改为每轮显式输出全部活跃operation及动态pane，不能归咎OA/WRC。
- 运行时缺陷验收完成；RTK业务任务仍实施中，未宣称产品Verify或最终E2E通过。前文“尚未完成”是部署前的历史记录。
- 合并后验证54 passed；sentinel53 passed。未push。
