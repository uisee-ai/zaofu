# Task Pipeline 启动合同路径补齐

> 状态: active

本轮真实并行后Inventory worker被通用launch prompt要求寻找task.md，但Task Pipeline briefing未列Read Order/绝对路径，worker在自己的worktree内空查找。Kernel事实上已把当前capsule发布在配置state_dir/task_docs下。监工第一条通知误将漏指针表述成未提供，已紧接更正绝对路径及dispatch/contract修订并保留两条审计；不更改运行态或worker代码。

- 补齐briefing现有合同路径 -> verify: 真实dispatch→transport测试先RED；Impl/Verify、初派/重试、输送失败恢复均能读取正确state_dir下同一capsule/source/progress。
- 保留受控读取和typed提交 -> verify: Read Order明确task.md不替代required artifact read ledger，不改task/operation/attempt身份及验收。
- 上线 -> verify: 聚焦模块/直接调用者、premerge及mock E2E；合dev，新进程加载；当前worker已读取task/source/progress，继续受控读取与实现。

## 验证

- 真实dispatch→transport回归RED复现缺少Read Order；修改后聚焦107 passed。
- dev-verify plan/run自动影响闭包61 passed（8个文件，含mock恢复控制循环），无跳过层；premerge 51 passed。
- 当前Inventory worker已实际读取task.md/source.md/progress.md与受控工件，并开始读取59表目录。两个operator通知最终作为一条队列消息待处理；worker在此之前已自行找到胶囊，不能把其继续执行归因于通知。
- Environment旧代际结果16:17:36已被接纳为dev.blocked evt-a021c8cd8a52；真实并行约3分04秒。后续只有Inventory继续，环境缺项与宿主资格仍未完成。
