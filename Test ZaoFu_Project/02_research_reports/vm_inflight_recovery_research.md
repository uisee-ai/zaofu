# K571 步骤 3：真实 ZaoFu 来宾在途恢复接线调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s4_isolated_vm_recovery_host.py`。后续 K577/K591 已记录在途恢复/guest panic 检测证据；早期载荷待验证不再代表全部现状，实机掉电仍开放。

> 状态：方法与接线已定向核对，载荷待隔离导入/来宾资格验证；本文件不宣称 OS crash 验收通过。K571-A/B 只证明
> QEMU/来宾启动、同 overlay 重启和 owned 进程清理。真实来宾内的在途 Run 恢复仍需
> 一次正式 host 测试执行。

## 结论

最小真实路径是：第一次启动在同一个私有 overlay 内完成正式 `zf start`、配置/任务准入，
由正式 CLI 产生一个已派发但尚未提交结果的 operation/attempt；冻结当时的
`events.jsonl` 字节前缀、`session.yaml`、Task/Run 身份和 sidecar 摘要；只杀本次测试拥有
的 QEMU；使用同一 overlay、同一只读 base image、同一 UEFI VARS 和同一离线 seed，再运行
正式 `python -m zf.cli.main start`（完整启动，不能 `--control-plane-only`）。恢复应由
`zf start` 的 boot reconcile、startup catch-up、Watcher 和 Kernel 正式消费旧事件，继续同一
Run/operation 的新 attempt，最终只结算一次。

这不是 `GeneralRoute.tick()`、父测试直接调用 `Orchestrator.run_once()`、watcher-only
重启或 terminal Given。`LiveGeneralRuntime.__init__` 与 `GeneralRoute.__init__` 是首次
bootstrap，会创建 SessionStore/config/git/凭据；第二次启动不得重新调用它们，否则会重置
夹具而掩盖恢复。

## 正式源码接线

* `tests/testkit/live_general_runtime.py:20-58` 改写唯一 tmux session、创建
  `SessionStore(...).create()`、生成 mock `cat`、设置 `PYTHONPATH` 和 CLI 命令；
  `:61-84` 的 `start()` 只应在第一次和恢复时调用正式 CLI；`stop()` (`:86-127`) 不能在
  QEMU 中断前调用，因为它会把 worker 正常收尾。
* `tests/testkit/general_route.py:56-106` 的构造和 `reopen()` 分别建立首次 fixture 与
  进程内观察对象；`reopen()` 不是正式恢复入口。正式恢复必须从来宾内执行 CLI。
* `tests/testkit/tmux_general_worker.py:40-110` 只在真实 stdin prompt 后读取精确
  `briefing_path/run_id` dispatch receipt，并在 gate 文件存在时停住；它可制造可靠的
  “在途”窗口，但不能替代 provider 语义，也不能直接写 canonical state。
* `tests/conformance/test_zaofu_testkit_s4_watcher_takeover_host.py:1-300` 已证明
  watcher-only kill 的 preserve-workers/full-start 合同；该测试只杀 watcher，不能作为
  整机死亡。步骤 3 必须让 worker、watcher、CLI 父进程随 QEMU 一起消失，再用同 overlay
  完整 `start`。
* `src/zf/cli/start.py:733-751` 的 `_requeue_inflight_for_start()` 在非
  `control_plane_only` 启动时调用 `requeue_stale_inflight_tasks`；`:662-704` 的
  startup catch-up 通过 durable offset 触发一次 idle tick；`:984-1009` 的 `loop.lock`
  与 `SingleOwnerProcessGuard` 防止旧 watcher 仍持有所有权。恢复证据必须包含这些正式
  入口产生的事件，而非测试手工补写。

## 事件与持久性边界

`src/zf/core/events/log.py:77-95` 的 `EventLog.append()` 在锁内 `open(..., "a")`、
`write(line + "\\n")` 后退出文件上下文；没有 `flush()`/`os.fsync()`。`EventWriter` 的
`_append_with_projectors()`（`src/zf/core/events/writer.py:115-119`）只是调用该 append
并运行投影器。故测试可以冻结并比较“当前读到的账本前缀”，但不能声称此前事件已获得断电
耐久保证；不得在业务路径增加额外 `sync`/`fsync` 来造绿。sidecar/atomic writer 的
`fsync(temp)+replace` 只适用于其自身文件，不改变 EventLog 合同。

恢复前保存：

1. `events.jsonl` 当前字节数、SHA-256、最后事件 id/offset（只读）；
2. `session.yaml`、TaskStore/Run contract 身份和 operation/attempt refs 的 SHA-256；
3. 既有 sidecar 的 ref/digest；
4. QEMU/base/overlay/OVMF/seed/项目解释器的 SHA-256；
5. 中断时的 QEMU PID、QMP socket、serial 和 supervisor 清理结果。

恢复后要求前缀字节不变，新增事件只允许沿同一 run/operation/attempt lineage；旧 attempt
只能被 supersede/retry 一次，新 attempt 不能生成第二个 terminal/settlement/candidate。
再次完整 `zf start` 后应无新的结算或 transport send（幂等重放）。

## 最小实现与 fixture allowlist

### 来宾与启动材料

* 固定 Canonical Ubuntu Minimal noble 镜像、SHA-256、OVMF CODE/VARS、QEMU binary 与
  loader；base 镜像只读，业务写入具名 qcow2 overlay。
* 一个只读CIDATA seed block内包含`user-data`、`meta-data`（可选`network-config`），
  可另有独立只读payload；不是每个seed文件各占一个block。NoCloud以
  `LABEL=cidata/CIDATA` 和 `TYPE=vfat/iso9660` 发现设备，不要求 `/dev/sr0`。
  源码：`cloudinit/sources/DataSourceNoCloud.py` 26.1，
  <https://github.com/canonical/cloud-init/blob/26.1/cloudinit/sources/DataSourceNoCloud.py#L51-L90>。
* 无NIC由QEMU `-nic none`保证，不靠cloud-init配置声称网络隔离。独立`network-config`
  应是无外层`network:`的version-1/2数据，例如`version: 2`与`ethernets: {}`；空网卡
  实际渲染及镜像既有netplan等待行为仍需guest验证，不能保证仅加文件就消除120秒等待。
  `network: {config: disabled}`属于`/etc/cloud/cloud.cfg{,.d/*}`系统配置；另一个
  官方入口是kernel命令行`network-config=disabled`，不是独立NoCloud seed的同名格式。
  user-data不能改变网络配置，不通过bootcmd事后写文件冒充first-boot网络配置。
  一手依据（2026-09-25主线程实际回读26.1源码文档）：
  <https://raw.githubusercontent.com/canonical/cloud-init/26.1/doc/rtd/reference/network-config.rst>；
  <https://raw.githubusercontent.com/canonical/cloud-init/26.1/doc/rtd/reference/datasources/nocloud.rst>。
* user-data 只写测试标识/启动探针，不写认证、不联网、不以 `os.sync()` 作为业务耐久
  证明。cloud-init 的输出可从串口和 guest journal 读取，但串口 marker 只是观察证据。

### 项目运行时

* 从 uv 管理的完整 CPython 3.11.15 base prefix 复制/只读挂载所需运行时，保留其 symlink
  和标准库；在 guest 内校验 `sys.version`、`sys.prefix`、解释器 SHA-256。不要把 host
  venv 或绝对 shebang 直接带入来宾。
* **不能只按pyproject基础PyYAML复制依赖。** 当前CLI实际导入HTTPX；
  `tmux_general_worker → GeneralRoute → issue_route/ModelEvidence`直接导入pytest及
  `hypothesis.errors`，故这两个包在来宾worker也必需。不是只打包标准库脚本。
  2026-09-25主线程在当前解释器独立导入`zf.cli.main`、`tmux_general_worker`、
  `live_general_runtime`后按`importlib.metadata`核对版本及非extra依赖闭包，初始allowlist：
  PyYAML6.0.3、pytest9.0.3、hypothesis6.168.0、httpx0.28.1、rich15.0.0、click8.4.1；
  闭包再含Pygments2.20.0、markdown-it-py4.2.0、mdurl0.1.2、anyio4.13.0、
  certifi2026.5.20、httpcore1.0.9、idna3.16、h11 0.16.0、typing_extensions4.15.0、
  sortedcontainers2.4.0、iniconfig2.3.0、packaging26.2、pluggy1.6.0。
  这是当前导入/metadata闭包，不是尚未执行的全guest动态路径证明；后续必须在重定位
  payload里实际导入、执行CLI帮助和正式配置准入前置，缺包即停止，不在guest联网安装。
  只复制这些发行版的受限RECORD路径与必要dist-info，不复制editable .pth、宿主绝对
  shebang、direct_url或整个site-packages。保留LICENSE/版本及逐文件SHA。
  项目本体为`src/zf`（含包内数据）和CLI实际需要的`src/issue_core`，不能连同仓库
  `zf.yaml`/运行态/认证复制。若使用mock tmux transport，guest还必须有实际`tmux`与git；不应把
  host 的 Claude/Codex、token、`~/.claude`、`~/.codex` 或 Feishu 凭据带入。
* 来宾全新HOME与合成公开测试token由夹具在overlay内创建，不复制宿主真实认证。
  必需的受控环境包括`PATH/PYTHONPATH/PYTHONUNBUFFERED/ZF_CLI_CMD`、私有workspace/
  tmux目录、`GIT_CONFIG_NOSYSTEM=1/GIT_CONFIG_GLOBAL=/dev/null`和禁用Feishu；
  worker的`ZF_STATE_DIR/ZF_ROLE_INSTANCE`及签发结果凭据沿正式派发入口，不删掉真实
  合同要求的签名上下文来求绿。第二boot固定相同guest绝对路径。
* `tests/testkit/evidence.py:25-44` 的 atomic JSON sidecar、
  `tests/e2e/scripts/prod_flow_terminal_audit.py` 的只读 terminal audit 可作为证据工具；
  不把它们当 kernel 状态写入器。

uv 官方 Python 分发说明：<https://docs.astral.sh/uv/concepts/python-versions/>；
python-build-standalone 可重定位性与运行时布局：
<https://raw.githubusercontent.com/astral-sh/python-build-standalone/main/docs/running.rst>；
<https://raw.githubusercontent.com/astral-sh/python-build-standalone/main/docs/distributions.rst>。
官方说明install_only含working/self-contained install树；不能据此跳过ELF、guest ABI或
重定位实测。当前宿主base prefix约98MiB，site-packages约140MiB，不能直接整树拷贝后
宣称最小或无secret。准入条件是来宾内实际启动该
解释器并通过版本/路径/SHA 校验，而不是只检查宿主文件存在。

## 一次最小可审计流程

1. 首次 guest boot：验证 NoCloud nonce、guest boot id、无网络接口（仅 `lo`），写入
   初始 identity sidecar；运行正式 `zf start`，等待 `loop.started`。
2. 正式 CLI workflow admission：沿 `test_zaofu_testkit_s4_watcher_takeover_host.py`
   的 proposal → `workflow start --apply` 路径创建 Run/Task；等待一个真实 worker
   dispatch receipt，确认 `submission` 尚不存在，记录 ledger prefix 和 operation/attempt。
3. 故障前保持gate存在、release不存在，不做人工提交；只中断 owned QEMU（监督器必须保留 PID/QMP/serial
   证据），确认来宾内 watcher、worker、tmux、CLI 全部消失，且不是调用 `zf stop`。
4. 同 overlay 重启 guest，重新校验 identity、boot id 变化、base SHA 不变、账本旧前缀
   SHA；在 guest 内运行正式 `python -m zf.cli.main start`，禁止父测试 tick。
5. 等待 startup catch-up/requeue、Watcher 和新 attempt；核验旧 attempt 的 supersede
   关系、同一 operation/run lineage、唯一 terminal/settlement、artifact digest 和
   owner delivery。随后再次完整 start，确认不重复结算/派发。
6. 只读收集 serial、events 前后缀、Task/Run/sidecar 摘要、QEMU cleanup 和审计 JSON；
   用独立 host oracle 比较身份/前缀/唯一性。不要把 fixture worker 的脚本输出当模型
   质量，也不要补写 terminal event。

## 失败分类与有限成功证据

* **夹具前置失败**：NoCloud seed 未枚举、guest Python/依赖缺失、tmux/loader 不可用、
  workspace/权限不满足、QEMU 非 owned 或资源预算超限。应停止该轮并保留 serial、命令、
  环境和清理证据，不登记为 ZaoFu 生产 bug。
* **产品恢复失败**：同 overlay 身份不一致、旧账本前缀改变、正式 `zf start` 未重排在途
  Task、operation/attempt 跨 run 串线、重复 settlement/terminal/transport send、
  startup catch-up 漏读或 stale guard 错误。只有这种情况才创建 ZF Issue，并绑定精确事件
  前缀和源码 SHA。
* **证据/耐久性不足**：EventLog append 读回成功但没有 fsync、监督器被 SIGKILL 后无
  final report、只杀 watcher、terminal 由 Given 注入、父进程代跑 `run_once`。这些只能
  判为非绿/不可证明，不能转换为产品通过或 crash recovery 通过。

有限“成功”只能是：来宾入口/解释器校验通过；同 overlay boot id 改变且 identity 保持；
正式 CLI 产生新 attempt 并保留旧前缀；最终只出现一个 terminal/settlement、全部 refs/digests
可读；第二次完整 start 无新增结算/发送；owned QEMU 与 guest 进程均清理。即使全部满足，
也只关闭 K571 步骤 3 的该故障模型，不外推 guest kernel panic、宿主断电、真实 Provider
网络/额度或所有平台恢复。
