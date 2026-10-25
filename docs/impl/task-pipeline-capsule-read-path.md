# Task Pipeline 的任务胶囊读取路径

Task Pipeline派发先发布当前task capsule，再生成briefing并发送prompt。2026-10-25真实并行验收发现prompt要求按Read Order读task.md，但briefing缺少此节，worker的独立worktree也不包含项目级状态目录，导致无效搜索。

`write_task_pipeline_briefing`现在从`runtime.state_dir`通过既有task_doc路径函数生成绝对task/source/progress路径；显式核对dispatch/contract revision，读取原Controlled Artifact Inputs，最后使用同一Output Contract提交typed result。它只投影已有路径，不创建第二套合同或写canonical state。

验证与真实记录：[任务](../../tasks/done/2026-10-25-1622-pipeline-capsule-read-path.md)。
