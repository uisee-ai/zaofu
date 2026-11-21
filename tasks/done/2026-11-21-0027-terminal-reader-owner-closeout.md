# 终结 fanout 的过期 Reader 占用清理

> 状态: done

用户授权8×8验证中的问题修复、记录和恢复；不合并dev、不push。

窗口9已由原生控制器生成新Candidate evt-62cd367e1e56，7路Reader实际运行，
lane1因reader_environment_busy未派发。旧fanout已失败，原operation仍suspended。
当前性索引证明旧候选已被kernel准入evt-3f815fec2b82替代；扫描却先跳过terminal
fanout，导致既有过期operation取消路径没执行。不是产品失败，也不是新Reader达到上限。

- 终结fanout加已准入替代 -> verify: 原生周期入口取消旧活跃operation、释放确切旧transport，保留原聚合failed。
- 无准入/其他Run/外部伪造准入 -> verify: 不取消、不终止transport。
- 重启重放 -> verify: 不重复终止，不停止新持有者、不产生假task.done/验证通过、不重置attempt次数。
- 实际部署 -> verify: 其余7Reader继续同operation，第1路原生派发，输入current；单独记录新观察窗口。

修复在独立源码worktree进行；控制器只在部署时短暂重启，保留7个运行中的Reader，避免消耗额外尝试。

验证：有效行为 RED 1 fail / 3 pass；focused 227 passed；相对 4d2d81d7 的自动影响闭包 612 passed / 7 skipped；sentinel 53 passed；deterministic flow smoke 10 passed。日志保存在隔离验证 reports 的 terminal-reader-owner-* 文件。只读真实选择预览仅选择旧 fanout 的 lane1，准入因果为 evt-3f815fec2b82。

部署前新 lane1 于 00:29:40 UTC 因 reader_environment_wait_exhausted 失败，尚无当前 operation。清理旧持有者后还需核实受控恢复入口，不能将原 lane1 的 operation 冒充新 lane1，也不能声称已经恢复。

实现：fcfb0abd fix: release superseded readers after terminal fanout。
真实验证：原生取消evt-a775b93fbea6绑定准入evt-3f815fec2b82，旧operation cancelled、旧aggregate failed，未重置尝试。后续7ec3fd16补齐首次交付入口后，evt-30beeb85dbba派发新lane1，其他Reader保持原operation；新占用校验未绕过。
证据：reports/eight-qualification-20261120T122929Z/unprepared-reader-wait-dispatch-proof.json、terminal-reader-owner-applied.json。
