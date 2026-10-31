# 产品文档主阅读视图

> 状态: active

批准依据：用户已明确授权本批次先建 backlog 后实施，无需再次审批。

来源：同批次用户授权建议2，依赖审阅正确性修复。

范围：产品摘要、业务模块、页面与设计、验收和待决事项成为导航主线；任务是业务规格的关联入口。提供章节导航、搜索、稳定链接和按需工程上下文，保留完整正文与下载。原始权威需求保持原版本，不将派生 Plan 冒充已完成独立 PRD。

验收：
- 从概览查找业务模块/页面/待决项 -> verify: 桌面与390px视口均可访问；正文无整体横向溢出。
- 阅读完整文档 -> verify: 真实标题目录定位、搜索定位与直达链接可恢复；超长文档不截断，Markdown 下载保留原文。
- 查看页面设计 -> verify: 显示已有页面、表单、参考图编号与设计/实现/验收状态；未提供的图或设计成果不伪造。
- 工程细节 -> verify: 可折叠且保留来源/附件/审批路径；阅读不发业务写请求。

实施验证：相关后端/API/mock E2E 94 passed；Docker Playwright 25 passed；sentinel 51 passed；前端 build/typecheck/unit 通过。
扩展 dev-verify：858 passed / 1 skipped / 1 failed，失败为基线已复现的 Feishu 无待审请求审批测试，未放宽 gate。
完整命令、范围与基线复现见 `docs/records/2026-10-31-prd-reader-validation.md`。未修改 ls 冻结 Plan，未部署或重启真实 workflow。
