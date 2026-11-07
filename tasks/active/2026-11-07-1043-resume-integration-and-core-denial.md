> 状态: active

# 恢复视频/留存候选集成及 Dataset 共享拒绝前置

用户明确授权定位 block、修复并继续运行。当前27/42 done，无运行worker。

## 验收

- 定位视频rolling smoke -> verify: 区分bootstrap环境超时与产品断言失败，保留原失败收据。
- 使用原锁hash补齐候选依赖 -> verify: 原smoke在候选通过且代码无未提交变化；正式recover task-integration接纳，Kernel完成候选集成。
- 排查留存Verify通过后停滞 -> verify: 确认队列/单写者/Task状态，沿正式恢复路径推进，不代签done。
- 修复Dataset gap准入 -> verify: 补充共享owner任务含rolling smoke和结构化AC及完整输入分片，保留已有Dataset实现；原正常Plan批准/派发链路通过。
- 持续观察 -> verify: 真正task.dispatched/operation运行，不仅进程启动；记录所有无法自愈问题及干预。

## 初始证据

视频失败收据eff3bbc...：VC-P16B-MEDIA-smoke 180009ms timed_out，exit_code=null。候选app/evidence/privacy_media/runs/20261107T062044-d63b5d78/bootstrap.log明确下载av38.4MiB/Pillow6.7MiB后300s超时，业务commands为空；两库当前均未安装。原候选源码HEAD ac21826c73a772e15fa9c57fcbb54e7563f10949且干净。不能把此次环境失败算产品RED。

Dataset flow.gap_plan产生的新增P09A-CORE-DENIAL-R1缺rolling_smoke标记，amend失败evt-610fe9a4b772；恢复链路仍待定位。详见本轮追加记录和0200监工任务。

## 已验证恢复（UTC 10:51）

- 候选确实缺 av/Pillow；按原 requirements.lock 的版本及 hash 校验已缓存 wheel，在 bootstrap.lock 下离线补齐两项，然后重新执行原 bootstrap 和原 privacy_media smoke：1 passed / 30 deselected。候选源码保持干净。未改锁文件或降低门槛。
- 首次环境脚本的逐字 lock 段比较误把空行格式差异当失配，后续 shell 命令仍启动了慢下载；已按 PID、父 PID、cwd 核实并终止本 session 的 installer，改为版本及 hash 集等价检查。保留 operator-install-correction.json，不掩盖此操作失误。
- 正式 integration retry evt-e5dc686bc322；run goal active evt-204efc9f7ea2；保留 worker 的控制面重载 evt-2e974a9c89d3，通过现有初始化解除 remediation safe_halt（dispatch.resumed evt-59c505fabaa1）。
- 留存集成 evt-a6510dae9720，done evt-03970788962f；视频集成 evt-dfd2210f1912，done evt-5fa380e5b3b1。当前 accepted candidate 4a99fd888e87ce546b64385f256ec6764dd334eb，29/42 done。
- 隐私后继 P16B-PRIVACY-R5 已正式派发 evt-67788999e691，运行 operation wop-impl-tp-P16B-PRIVACY-R5-6b86a6e777b84-2dd522c809e2。8/8 容量不代表无视依赖强行派发。
- 新定位全局暂停根因：无 active task/dispatch 的 on-demand Verify heartbeat 被判 stuck，重生成功熔断后触发 safe_halt。已安排实际 caller 回归修复，仍需提交、加载和观察。
- Dataset 准入失败还遗漏 task_map.amend.failed 的 watcher/kernel 入口，已提交 f739a2ca fix: route taskless gap rejection through kernel recovery（归档3a8ce2d9），本次需与 idle 修复共同加载。历史已消费失败不伪造重放；准备基于实际拒绝的一次有界 owner 授权 Plan 修订。

完整环境/集成恢复证据：/home/min/uisee/zaofu-echo/datal/diagnostics/recovery-20261107-1043/；runtime ledger 为最终状态来源。Dataset 修订尚未通过 Plan/派发，不标本任务 done。

## Source 补丁加载（UTC 10:56）

- a3816e1c fix: prevent idle verify workers from triggering safe halt；归档4d05010f。真实sweep/direct stuck先判断on-demand typed/legacy活动归属，保留未知状态检测、resident/eager与活跃reader。203 focused、50 sentinel/1skip、10 mock通过；真实6条operation回放旧guard=true/new=false，0 stuck/pause。
- f739a2ca 与 a3816e1c 只同步精确caller hunk及新helper/test，保留main现有不同修复；原始文件及hash清单存persistent diagnostics/source-load。Main组合回归77 passed。
- Main额外sentinel为49 passed/1 skipped/1 failed：既存 orchestrator_reactor.py 6981行超过6977冻结值。本次没有修改该文件；与隔离树相比，main保留此前completion recovery hook5行和suspended状态兼容，不能为清绿删除功能或放宽baseline。本轮未merge，记录此源树结构债，不把main sentinel描述为全绿。
- 控制面保留worker重载 evt-8c519e83ffae，actuator2315732；重载前Privacy-R5 operation仍running。重载结果及恢复后的相同dispatch需继续核验。

## Dataset 有界恢复请求

- 预检最新accepted candidate4a99、包678、29done、287AC/27决定；唯一活动Privacy不在Dataset+11个下游的机械恢复scope内。请求仅规划Core-denial前置与Dataset末端替代，不改11下游业务合同，不重开done。
- 已调用既有 candidate-rework-apply/replan，结果applied：orchestrator.replan_requested evt-0f6980b8dee1，Planner入口 prd.scan.completed evt-44b1c6e3e0f8（复用旧scan，不重跑广泛scan），result evt-46db1f085fdb。checkpoint operator-dataset-core-denial-recovery-20261107。
- 原失败6b80937仅是待移植与重验的Dataset实现，不当accepted基线；所有新增任务要求rolling smoke、结构化AC和required输入分片。Plan人工审批门仍在；用户委托监工审批将在Critic及最新候选/并发任务兼容检查之后。
- 控制面重载后watcher2315732、Privacy原operation/attempt仍running，未新建或取消其任务。Planner实际派发与完成仍需观察，不把applied请求当作产品修复完成。

## 实际推进确认（UTC 10:58）

新Planner已正式派发：fanout.started evt-f6a156af7b60，workflow.operation.started evt-0471508c3ca2，fanout.child.dispatched evt-b6edee4f096a；fanout-prd-plan-evt-44b1c6e3，operation wop-prd-plan-prd-planner-trig-evt-44b1c6e3-re-20b8d53f5872。Privacy仍以原operation发送10:58:31 heartbeat evt-f10d5609f3b1，未取消或重开。当前29/42 done，剩余13个有效产品任务（另有父协调Task和历史cancelled不计）。

本任务继续active：环境与运行时修复已生效，但Dataset的Plan/Critic/批准、Core前置实施和后继独立验证尚未完成。最终新图批准须重查candidate是否前移及Privacy合同/结果兼容，不直接复用本次请求预检代替审批。
