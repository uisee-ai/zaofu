> 状态: done — 6492d40f fix: preserve planner sessions only within proven workflow context

# Planner 同任务会话连续性与旧上下文隔离

用户明确要求修复 Planner 未复用旧会话，防止后续恢复继续重复读取或误用旧上下文。
基线 49ca0fdd；沿用本工作树，不修改主目录脏文件，不打断当前正在运行的 Planner。

## 现场事实

原Run workflow-ddd320aa0dc7c2ef；2026-11-23 00:42:16–20，planner 在同一Task/Run的新派发上产生
reader_dispatch_context_changed、codex_no_cached_session、provider_session_resume_not_proven。
有效配置 planner.lifecycle.reader_context_scope=dispatch，故内核按显式配置清空UUID；不是历史rollout一定丢失。
原始报告为运行worktree reports/eight-unattended-20261121T132814Z/autonomous-plan-context-followup.json。

## 范围与验收

- [x] P1 根因及配置修正 -> verify: 用真实多文档zf.yaml加载复现；Planner按workflow保留，独立Verify/Judge的dispatch隔离保持；配置不改变模型、命令、权限或验收。
- [x] P2 原生会话绑定与隔离 -> verify: 实际fanout派发入口同Run/Task不清UUID；跨Run/Task、缺失身份不能继承旧会话；既有Provider配置/rollout检查仍拒绝不兼容resume。
- [x] P3 当前版本输入 -> verify: 会话复用不继承产品通过票、不跳过新attempt的必读/版本门禁；明确区分session恢复和跨attempt阅读复用，未实现的不承诺。
- [x] P4 验证、提交与安全生效 -> verify: 聚焦影响闭包、哨兵及相关mock；提交实现/记录；保护当前执行，配置在安全检查点加载并记录实际生效证据。

## 边界

zf.yaml是唯一控制面；不能让代码偷偷忽略dispatch配置。恢复的会话仅提供上下文，当前Source Manifest及授权仍决定可消费输入。
本轮不用旧UUID覆盖新正在运行的会话，不重写旧读取账本，不提前宣布无人交付成功。

## 实施及验证记录

修正具名Planner配置生成源，而不是忽略显式dispatch策略。Run必须非空且相等才证明
旧上下文可复用；去掉taskless workflow早退；无Task/Run明确defer；缓存UUID但无旧
scope时不能直接认领，仍须通过既有活动turn、child、worker和退出检查。
复现测试先证明UUID错误清除、身份未知继承及taskless绕过，再修正实现。

聚焦8个测试文件186 passed（含Provider原生resume、配置变更、必读续读和上下文恢复）；
本轮使用49ca0fdd作增量基线执行dev-verify，已检查base dev计划。此前同分支相对dev的
12142项影响闭包已有通过记录，不因本轮小补丁重复整批历史验证。本轮闭包最终结果及
安全部署证据记录在reports/planner-session-continuity/，未运行全量pytest或新provider业务验收。
现场控制面在01:03自行部署8c13702c；本轮部署必须保留该修复以及当前Planner UUID。

最终增量闭包438 passed、7 skipped；哨兵53 passed；三流mock 10 passed。未改变当前
Source Manifest、必读账本和结果准入实现。研究session完成只读交叉审查，补齐了taskless
Plan Synth可达路径的隔离测试；未让研究session操作运行状态。

通过受控run-pause/run-resume与已有control-plane-only启动部署，合并运行基线为
d5560824（包含6492d40f及原8c13702c），没有合并到dev或push。
启动证据evt-25c7167d06d2的source revision、源码树摘要、配置文件摘要和有效配置摘要
均吻合；Planner的workflow策略已加载。
UUID 01a205da-8e61-71e0-b0ab-3cc031e77519与pane %144 / PID 304036保持一致；
同operation仍running，16项原Task全部done。原closed_failed无人资格记录摘要未变。
完整部署证据：运行worktree reports/eight-unattended-20261121T132814Z/planner-continuity-deploy.json；
副本及验证日志：本worktree reports/planner-session-continuity/。

本条done只代表会话策略修复、测试与安全加载完成。下一次自然replan尚未发生；跨attempt
免读和delivery envelope实际会话绑定的后续优化未包含，本轮不授予其读取复用权。
真实业务交付与无人资格仍由上一条active任务跟踪，不以本条done代替最终验收。
