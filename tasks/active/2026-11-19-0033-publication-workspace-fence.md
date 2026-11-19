> 状态: active

# 发布按实际工作区确认静默

承接用户批准的 P2 真实验证中发现问题、修复并重新验证的范围；源码基线 dev c5180ffc。
本轮冻结验证保持运行，不热改其源码或结果。来源：TOOL-MAX 的 evt-d1c5a220fef1、
evt-b421e5cbebd6、evt-3bbdba067b3f、evt-fddf71aa54bf；独立 mock 已复现不同 Task
复用同一 worker 时，旧工作区发布被另一个工作区的活跃 turn 延迟。

## 验收

- 同一 worker 转到不同 Task 的独立工作区 -> verify: 只有 kernel 派发、当前 operation、
  实际 hook session/turn/cwd 与因果绑定均一致，且旧工作区没有进程持有者，才能发布旧候选。
- 活跃 turn 属于原 Task、同/重叠工作区、来源未知或绑定不全 -> verify: 保持等待，不执行 Git。
- 发布与宿主检查走同一受控动作入口 -> verify: caller 回归覆盖；暂停、摘要、授权、未知效果、
  现有 Task 所有权与环境锁保持有效，发布不产生业务通过。
- 定向测试、影响闭包、哨兵、相关 mock -> verify: 记录真实结果；本项不替代 P2 的真实8×8资格。

非本项范围：自动额度探针、OA语义分类/陈旧提示、改变任务合同或制造并发时长。
说明：[持续推进与无人资格](../../docs/impl/unattended-progress-recovery.md)。

## 验证记录

冻结c5180ffc源码的隔离回归：2 failed、7 passed；修复初轮65 passed，补充工作区
重叠/越界/同Task/多个owner负例后专项13 passed。dev-verify影响闭包1275 passed，
哨兵53 passed，流程/8槽位mock6 passed，实际子进程并发mock5 passed。
证据：主工作区reports/publication-workspace-20261119/validation-summary.json。
首次测试误用共享venv的editable路径触发9项import防护错误，不作产品RED；
一次基线测试与编辑重叠导致9 passed，不作基线证据，随后已用冻结源码独立重验。
本项修复范围通过；原P2真实资格仍active，未声称本补丁经过真实Provider验收。
