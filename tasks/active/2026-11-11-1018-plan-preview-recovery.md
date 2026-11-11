# Plan Preview 规划冲突、恢复与真实复验

> 状态: active

用户已批准执行上一轮建议1–4。基于 ebcfd415，在当前spec206工作分支继续；依据 reports/spec206-optimization-20261111/evaluation.md。只本地提交，未授权merge/push；真实Provider测试已授权，终点为待审批或真实待决Preview，不实施示例产品。

## 验收

1. 来源AC与任务AC粒度 -> verify: 10项来源AC/上限8可通过保留全部原子场景和来源映射合理归组；实际互斥条件仍保留Owner决策；不硬编码业务数量判据或自动修改审批配置。
2. 规划失败与恢复 -> verify: 明确执行完成的语义阻塞不冒充Provider故障；绑定草案可交独立Critic审查并进入现有Owner检查点；真实执行失败仍受保护；同Run/版本/摘要、幂等和重放反例通过。
3. 多任务与测试脚本 -> verify: 首片矩阵不消费未来产物；共享文件规则据实际runtime能力处理，不能擅改README时点；PRD终点不依赖自由命名ID；未知Owner问题取证停止；明确阶段终止不继续等待。
4. 验证 -> verify: 聚焦影响闭包、sentinel、相关mock E2E和Docker先通过；冻结版本后顺序执行真实Channel小PRD、Kanban小PRD、多任务，保存原始失败/返工/候选和Web证据，明确passed/blocked/failed边界。

## 责任边界

归组、原子场景责任与Owner必要性由Skill/Agent判断；runtime只校验明确声明、工件绑定及既有转换。单一Task合同，保留文件独占和受控动作保护。临时项目8002+，8001不动。

## 实施记录

1–3 已实现：Skill 归组/责任方法、显式 owner_review 草案预检、PRD 选项和终止等待修正。
聚焦校验、提交/绑定/恢复、mock E2E、构建及 sentinel 通过；4 的真实复验仍待完成，保持 active。
证据目录：reports/spec206-recovery-20261111/。未 merge/push。

真实首轮55af3551：AC归组生效，已进入两轮独立Critic。真实证据归属冲突已由运行内返工修正；
Critic连续误读V-03的显示转义，已用绑定工件及静态解析反证后停止测试，不能记passed。
补接Kanban PRD方法、Owner畸形字段诊断及评审复现/反证方法，继续新冻结版本的真实三轮。
