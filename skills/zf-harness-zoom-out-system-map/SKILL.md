# Skill: zf-harness-zoom-out-system-map

> Sprint: ZF-SKILL-MP-008 (doc 39 §4.7)
> 目标角色: dev / review / test
> 状态: net-new (P2)

## 目的

陌生模块动手前先产出**短系统地图**，避免局部误改。

"短" = ≤ 30 行 / ≤ 5 个文件引用。**不是**写架构白皮书。

## 操作规约

worker 收到 dispatch 后，如果发现要改的模块/文件**自己不熟悉**：

1. 用 `Grep` / `Read` / `git log --stat` 5 分钟内构建：

   ```text
   # System Map (TASK-X — module: <module_path>)
   ## Entry points
   - <file>:<line> — <一句话什么作用>

   ## Data flow
   - input: <where>
   - mutation sites: <files>
   - output: <where consumed>

   ## Tests covering this
   - tests/<test_file>.py::<test_name>

   ## Recent commits
   - <commit_sha> <commit_msg>
   ```

2. emit 一条 `memory.note`:

   ```bash
   zf emit memory.note --task <task_id> --actor <instance> \
     --payload '{"category": "system_map", "module": "<path>", "summary": "<10 行>"}'
   ```

3. 在 progress.md 里能看到这条 note 时 → 实际动手改。

## 反模式

- ❌ 一头扎进 `src/zf/runtime/orchestrator_dispatch.py` 改 50 行而
  没读上下文
- ❌ 系统地图写 5000 字（要 ≤ 30 行）
- ❌ 改完才意识到 caller 有 N 个，需要回退

## 关联

- ZF-PWF-MEM-001 (memory.note 进 progress.md projection)
