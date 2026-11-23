# Reader 在准备 operation 前等待超时的恢复

> 状态: done

本项沿用用户对8×8真实验证问题修复与恢复的授权；隔离工作区实现，不合并dev、不push。

真实失败 evt-e7faceadbc3b：当前 Candidate lane1 在 pin target 阶段等待旧环境，尚未创建 operation；等待628秒后原生超时。旧持有者已由 fcfb0abd 和原生 evt-a775b93fbea6 清理，但 child-rebuild 强制要求不存在的 operation，使恢复无法承接。不能使用旧 fanout 的 operation 顶替。

- 控制入口绑定未准备的失败 -> verify: 校验Run、当前fanout、Kernel start/expected-child、原等待事件、pending run_id、最新失败、从未派发且从未创建operation。
- 原生首次派发 -> verify: 正常目标pin/合同/权限/attempt路径建立新call，因果指向真实失败；不创造原operation或成功回执。
- RM建议与执行 -> verify: 经同一受控准入，执行再检查；观察准确的后继terminal效果，重放幂等。
- 外部/旧版本/已派发/已有operation/暂停Run -> verify: fail closed；不靠省略字段绕过attempt上限。
- 真实部署 -> verify: 保留其他Reader和业务完成证据，只恢复第1路；记录人工动作和实际派发，原始无人窗口仍未通过。

有效行为RED：1 failed / 7 passed。

验证：262项定向通过；相对已部署fcfb0abd的自动增量闭包4312 passed / 9 skipped，sentinel53 passed、flow smoke10 passed。随后补充保留旧Task child-rebuild入口的执行检查，最后57项相关回归通过。实际现场只读preflight通过；原生派发及效果仍待部署，不称全量验证或无人通过。

实现：7ec3fd16 fix: recover reader waits before operation preparation。
真实验证：实际受控恢复后evt-30beeb85dbba以evt-e7faceadbc3b为因果派发lane1 retry-1；首次durable operation a92a4dc40f66运行，目标09094c2d/current输入有效。旧failed和16业务完成证据保持，未伪造前序operation。验证的是恢复首次派发，不代表Candidate/Judge已通过。
证据：reports/eight-qualification-20261120T122929Z/unprepared-reader-wait-dispatch-proof.json、terminal-reader-owner-applied.json。
