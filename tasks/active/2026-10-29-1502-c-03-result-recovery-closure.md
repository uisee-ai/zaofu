# C-03 WRC result admission 与异常恢复闭合

> 状态: proposed

## 目标

证明 WRC blocking 不只负责“发出去”，还能够依据 typed result、lease/heartbeat 和 generation 继续推进；异常时交给 Run Manager 恢复，旧 attempt 不能污染新代际。

## 范围

- 补齐 owner 标记在 worker execution envelope、结果接纳和 retry/rework 事件中的贯通检查。
- 覆盖 success、failure、stuck、respawn failure、unknown-is-not-idle、旧 generation result 的 mock E2E。
- 保留 Run Manager 作为 recovery owner；正常成功交接不启动额外 OA turn。

## 验收

1. stage success -> verify: WRC 被 EventWatcher 唤醒，后继 ready stage 进入同一 generation 的 dispatch。
2. stage failure / lease expiry -> verify: typed failure 与 recovery owner 可重放，按既有 retry/rework 上限处理。
3. unknown / old generation -> verify: hold 或 stale rejection，不进入 integration/terminal。
4. focused lifecycle、recovery、generation 与 mock E2E 通过；真实 provider 仍单独标记 qualification。
