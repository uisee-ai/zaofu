# Hello World PRD smoke

## 目标

用一个 Python 标准库命令行程序验证 ZaoFu 完整 PRD 交付链路。只规划一个实现任务，不增加额外功能。

## 产品需求与验收

- 在项目根目录创建 `hello.py`，仅使用 Python 标准库。
- 从项目根目录执行 `python hello.py`，精确输出 `Hello, world!`，退出码为 0。
- 从项目根目录执行 `python hello.py --name ZaoFu`，精确输出 `Hello, ZaoFu!`，退出码为 0。
- 使用标准库 `unittest` 提供自动化测试，实际执行上述两种命令并检查各自的输出与退出码。测试入口为 `tests/test_hello.py`。
- 从项目根目录执行 `python -c 'import sys,unittest; s=unittest.defaultTestLoader.discover("tests",pattern="test_hello.py"); n=s.countTestCases(); r=unittest.TextTestRunner(verbosity=2).run(s); sys.exit(0 if n and r.wasSuccessful() else 1)'`，命令退出码为 0；测试须覆盖上述两种调用。该命令会在没有发现测试时失败。

## 交付与流程约束

- 目标项目为本仓库；实现范围为 `hello.py` 和测试文件 `tests/test_hello.py`，如测试发现需要可使用 `tests/__init__.py`。
- 使用目录中的 `ZaoFu PRD Workflow v5 Production (Codex)`，标识为 `zaofu-prd-v5-production-codex`，配置来源为 `zf-prd-v5.yaml`。保留 Scan、Plan、Plan Critic、OA pre_impl、Impl、独立 Task Verify、Candidate Verify、Discovery、Judge 和 Kernel 交付。
- 保留该配置的默认 lane 容量、模型和生命周期：8 条 lane、impl 和 verify 池容量各 8、最多 16 个活动 Task Pipeline，worker 为 on-demand 且空闲 120 秒；模型与推理强度沿用模板。不要求并行任务，也不切换到 light 或直接派发。`plan_approval=false`。
- 创建 Task 后先准备完整配置预览；配置变更须经过受控审核和应用，启动须由 Owner 在页面单独确认。预览应保留本项目的项目身份和运行态目录，并将模板中与本项目不匹配的项目路径、验证命令和 Git 引用适配到实际环境，不降低验收或独立验证要求。
- PRD 和 Task 的来源、handoff 引用使用项目相对路径 `docs/prd/hello-world-prd-smoke.md`；预览及启动前需确认它可由各工作树通过受控输入机制读取。
