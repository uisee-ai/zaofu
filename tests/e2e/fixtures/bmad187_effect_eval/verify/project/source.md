# 已批准的本期规格

AC-TXN：库存转移扣源、增目标、记流水必须全成功或全回滚；错误时 state 不变。
AC-UI：通过 transfer_and_present 组合入口完成转移，并展示转移编号。
AC-COUNT：现有 query.total 返回库存总数，行为无变化；补充测试作为回归即可。
本期不含通知，Owner 已明确阶段 Goal。Candidate 必须验证以上三个 AC。
Task 局部测试通过不代替组合行为；Judge 消费已接纳的当前证据，不另行运行产品测试。
