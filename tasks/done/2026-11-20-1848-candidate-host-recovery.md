# 候选宿主恢复的唯一owner与资源配置

> 状态: done — `5a0a9682` fix: preserve candidate host recovery ownership and evidence

真实Run workflow-055abbd5196f67c6：18:43:54浏览器宿主操作ecdd74db启动，18:44:40 watchdog杀死控制器，操作结果未知。18:45后另一个较早注册但稍后失败的Reader抢占宿主队列，重复执行已通过命令。产物mobile-trace.zip为24,215,018字节，旧20MiB逐文件限制也不足以容纳冻结浏览器脚本输出。用户已授权修复、评估和断点恢复。

- 整个Candidate cohort先选择已有宿主操作owner再执行 -> verify: 操作注册顺序/失败先后不影响唯一owner；异Run/fanout/target/contract不合并；未知结果不自动重试。
- 同步宿主命令timeout与watchdog一致 -> verify: 启用两者时，watchdog不得小于等于宿主命令上限；仍仅完整主循环后喂心跳，不新增后台假活。
- 显式有界采证大小 -> verify: 默认20MiB逐文件保持，增加总量上限；授权配置可容纳真实trace；超限、非法类型/值、符号链接继续拒绝。相同执行策略下原较严格默认采证收据可以继续引用，不能视为新的产品verdict。
- 原窗口已于受控暂停前关闭 -> verify: 停止记录与未知宿主操作保留；16项业务和原Candidate不变，后续通过正式新验证代际接续。原窗口不追认无人通过。
- 聚焦、影响闭包、哨兵、mock流程与独立真实接续分别记录 -> verify: 不用单元测试替代真实Docker、Judge、Goal和交付验收。

验证：实际入口回归6红转绿；owner乱序回归1红/9绿转全绿。合并聚焦121项通过，补充owner未知结果/乱序与限制50项通过（有重叠）。影响闭包首跑13,210 passed、17 skipped，因测试环境缺少controller依赖有13收集错误和1失败；使用依赖齐全的实验venv对14个受影响文件补跑182 passed。Web typecheck/unit、53哨兵、10流程、2项真实Docker执行器检查通过。重复后端验证因错误tier名被操作者停止，已用正确deterministic tier完成剩余检查，未停止运行态进程。当前代码尚待窗口外部署，真实接续结论单独记录。

真实接续：新c6870392 cohort的19条宿主命令均由lane6同一owner完成。浏览器19:50:27开始、19:51:08正式收据evt-db22dcbf5981，exit0、source_stable=true、候选09094c2d、固定镜像；桌面/移动断言通过，全部media摘要校验。控制器PID2142947保持且NRestarts=0。本次最大trace约16.9MB，证明正常正式采证；超20MiB边界仍以既有确定性测试和历史24MB故障为依据，不虚称本次命中该边界。未知旧effect保留。证据reports/eight-qualification-20261120T122929Z/browser-formal-evidence/provenance.json及final-effects-current-window.json。Candidate/Judge/Goal/本地交付仍待验，不计入这项修复的执行证明。
