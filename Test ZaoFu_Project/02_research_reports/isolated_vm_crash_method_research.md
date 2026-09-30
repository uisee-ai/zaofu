# K571：临时隔离来宾 OS crash / takeover 方法调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/isolated_vm_run.py`。隔离 VM 驱动已存在；owned QEMU SIGKILL、guest panic、guest 文件系统损坏和物理掉电必须分别判定。

## K590 补充：guest panic 能力与真实恢复接线边界（2026-09-28）

本轮回读 Linux v6.8 官方 SysRq 文档和 QEMU v8.2.2 QAPI：
<https://raw.githubusercontent.com/torvalds/linux/v6.8/Documentation/admin-guide/sysrq.rst>；
<https://raw.githubusercontent.com/qemu/qemu/v8.2.2/qapi/run-state.json>。
Linux 文档明确 `/proc/sys/kernel/sysrq` 只限制键盘入口，管理员写 sysrq-trigger 不需要先
开放全部 sysrq；所以新探针不改宿主或 guest 的全局 sysrq 开关。guest 通过 DMI UUID 与
本轮 nonce 匹配、网络仅 lo、crash kernel 未加载，再固定 panic=0，避免自动 reboot/kdump
扰乱故障判定。QEMU 仍按显式 `panic=shutdown` 退出，此动作不等于物理断电。

实际私有 QEMU 的 pvpanic 支持、guest driver 与串口已得到能力证据；最终 QMP
`GUEST_PANICKED.action=poweroff`，不能把文档示例的 pause 写死为唯一合法值。
必须同时要求 SHUTDOWN 的 reason=guest-panic，而非正常关机。报告和失败修复见
`steps.md` K590、`attachments/k590-panic-20260928/`；能力探针没有运行 ZaoFu。

下一条真实恢复测试必须先完成既有 firstboot scenario 和 guest launcher 的
`firstboot_complete` 观察 sidecar，再在 bootcmd 的受控来宾分支触发 panic。
不能在 scenario 提前 panic：否则 launcher 尚未写完 firstboot marker，restore 会正确拒绝，
测到的只是夹具缺口。触发前同时绑定 checkpoint Run/attempt/boot_id 与 nonce；原业务
ledger 不额外 sync。host 要等 QMP/串口 panic 和 owned exit，才能启动同 overlay 的第二boot。
第二boot复用正式 CLI 恢复 oracle：旧前缀、同 operation 的新 attempt、唯一结算/交付和
再次 startup 幂等。能力 marker、正常退出或测试层发出的成功文本不能替代这些断言。
本轮探索性接线未保留到原 runner/scenario；两文件保持修改前字节，不把未运行候选算实现。

> 日期：2026-09-25。只读官方资料、包元数据及本机能力；没有下载镜像/软件包、安装、
> 启动虚拟机、执行故障或修改宿主配置。本文是可实施路线，不是 OS crash 验收证据。
> 依最新分工使用本文件名；没有另建 `os_crash_vm_method_research.md`。

## 1. 结论与本机前置

可进入下一阶段的路线是：**现有 apt 索引固定版本 → 普通用户下载少量 `.deb` →
在单一临时目录解包 → 校验官方 cloud image → 私有 qcow2 overlay/OVMF VARS →
NoCloud 只读 seed/payload → 无网来宾 → 私有 Unix QMP/串口**。全程不需要安装宿主
服务、桥接、sudo 或可写共享目录。先只做工具/依赖及启动资格核对，再按正式计划
决定是否启动；不要把“已能解包 QEMU”报告成“OS crash 完成”。

本次只读事实：

- Ubuntu 24.04 noble、x86_64；未安装 `qemu-system-x86_64`；有 apt-cache、dpkg-deb、
  busybox、gpg、getfacl，没有找到 cloud-localds/genisoimage/xorriso。
- apt 索引候选：QEMU `1:8.2.2+ds-0ubuntu1.18`；`qemu-system-x86` 包约 11.22MB，
  `qemu-utils` 约 2.22MB；还需要 system-common/data、依赖库及固件，不能只取一个
  可执行文件。OVMF 候选 `2024.02-2ubuntu0.9`、约 5.19MB。这是当前索引，不保证之后
  镜像仓库仍提供同一版本；404/哈希失败应停，不全局 `apt update/install`。
- `/dev/kvm` 为 root:kvm 0660 **并有 ACL `user:uisee:rw-`**；当前 `test -r/-w` 均成功。
  因此“不在 kvm 组所以不可用”不成立。但未做 KVM ioctl/VM 创建，尚不能说加速器
  已验证。下一阶段不改组/udev/ACL，KVM 能力不满足则明确使用 TCG。
- `/tmp` 约 34GiB 可用，MemAvailable 约 18GiB，但 8GiB swap 已用约 7.4GiB。
  swap 紧张要求保守 RAM 和独立守卫；这些数字不是可长期占用承诺。

## 2. 工具临时提取：可行，但不是完整安装

Debian 官方说明 `apt-get download package=version` 将包下载到当前目录；
`dpkg-deb -x package.deb directory` 只提取文件树，不等于安装，也不会运行正常
包配置流程。[A1][A2] 推荐具体流程（尚未执行）：

1. 新建唯一 `/tmp/zf-k571-vm-<UTC>-<随机>/`，权限 0700；所有包、库、固件、镜像、
   socket、日志都在其子目录。记录绝对路径/所有者，不复用旧试验目录。
2. 用现有受签名索引的 `apt-cache policy/show/depends` 和只读模拟解决候选版本、
   Depends/Pre-Depends/替代依赖。`apt download` 不递归替用户安装依赖；必须形成
   明确 package/version/architecture/SHA256 清单，仅下载缺失或版本不兼容部分。
3. 起点包含 `qemu-system-x86`、`qemu-utils`、对应 `qemu-system-common/data`、
   `ovmf`；按依赖解析加入 `seabios`/`ipxe-qemu` 和动态库。只取 headless 必需路线，
   不因为推荐包安装 GUI、libvirt、bridge 或 qemu 服务。NoCloud ISO 制作工具可同样
   临时提取 `genisoimage` 或 `cloud-image-utils` 及其依赖。
4. 在临时 `root/` 解包，先列包内容/权限/symlink，拒绝任何路径逃逸；不执行包内
   maintainer scripts，不写 `/usr`、`/etc`、dpkg 数据库或全局链接器缓存。
5. 固定绝对 binary/firmware 路径。用受验证包的 ELF dependency listing 核对 loader
   和库；若需私有库，优先进程局部 loader `--library-path`，不向全局环境导出
   `LD_LIBRARY_PATH`、不运行 ldconfig。记录共享宿主库版本与私有库哈希；不可为一个
   缺失库笼统混入另一发行版 libc。QEMU 模块/固件路径也必须解析到已验证临时树。
6. 下一阶段先执行 `--version`、`-accel help`、`-machine help`、`-device help` 等
   不启动 VM 的命令；检查 OVMF code/VARS 实际文件名、QMP 所需设备与可用加速器。
   失败归类为 harness 前置缺失，不通过升级全机依赖解决。

QEMU `-L` 指定 BIOS/VGA BIOS/keymaps 目录；它不能代替所有固件路径和动态库检查。
OVMF 建议明确指定固件 code，读写 VARS 必须复制为本次私有文件。[Q1]

## 3. 来宾镜像、载荷与持久状态隔离

官方 `noble/current/` 此次索引列出 `noble-server-cloudimg-amd64.img`，约 596M，描述
为 **QCow2 UEFI/GPT Bootable disk image**；不应沿用未核对的 BIOS 启动假设。[U1]
推荐固定一个实际构建 URL/日期及摘要，不把会变化的 `current` 当最终 identity。

下载前核对预计总量；下载后按 Canonical 文档先验证 `SHA256SUMS.gpg` 的签名/官方
完整 fingerprint，再校验该镜像 SHA256。[U2] 本机无 ubuntu-cloudimage-keyring；
若需取得 key，使用临时 `GNUPGHOME` 或临时提取官方 keyring，不写用户长期 keyring。
从未认证的同一来源拿 key 和 checksum 不能自行构成信任链；fingerprint 必须对照
Canonical 官方资料。签名、镜像或 manifest 缺失/漂移即停止。

持久化布局：

- base image 只读并留 SHA；为每个实验建一个具名 qcow2 overlay，明确 `-F qcow2`
  和绝对 backing path。恢复使用**同一 overlay**，不换回初始快照来伪装恢复。
- 不使用 `-snapshot`：它把写入放临时文件且退出丢失，不适合作为跨 QEMU 进程 takeover
  的持久账本。禁止 `commit` 写回 base；镜像检查只用无修复模式，不 `check -r`。[Q2]
- OVMF code 只读，VARS 每个实验单独复制；不直接复用共享系统固件变量文件。
- `CIDATA` ISO9660 只读 seed 含 `meta-data`（唯一 instance-id）及 `user-data`。
  cloud-init 官方支持该 NoCloud 离线方法，无需网络 datasource。[C1]
- 另一个只读 ISO/payload 装精确 source/test fixture 和离线依赖，guest 复制到自身
  overlay 后运行。不得挂载宿主仓库为可写 virtiofs/9p，不复制真实 `.zf` 状态、
  Provider token、SSH key、配置秘密或无关源码。打包前按 allowlist 和 SHA 清单审核。
- 官方 manifest 包含 Python 3.12、cloud-init 和 tmux，不等于满足 ZaoFu 当前 Python
  3.11 依赖合同。[U3] 可把已存在的独立 CPython 3.11 与精确依赖作为只读 payload，
  但必须验证其实际解释器/共享库/路径可重定位；不能直接复制含宿主绝对 symlink 的
  `.venv` 并宣称可用。guest 内禁止联网自动补包，依赖缺失即返回前置失败。

初始虚拟磁盘大小应由 `qemu-img info --output=json --backing-chain` 实际核对；
若 base 虚拟容量已超过建议值，不擅自 shrink。**新增宿主实际占用总量 ≤8GiB**，
包含包、提取树、base、overlay、seed、payload 和日志，不只是 qcow2 虚拟容量。

## 4. 最小无网启动形状（供后续实施，不在本轮执行）

以 private-root 下的已核对 QEMU 为入口。参数形状：

```text
<private-qemu-system-x86_64>
  -machine q35 -accel <已验证的 kvm 或 tcg>
  -smp 1 -m 1024 -display none -monitor none -nic none
  -qmp unix:<run>/qmp.sock,server=on,wait=off
  -chardev socket,id=serial,path=<run>/serial.sock,server=on,wait=off,logfile=<run>/serial.log
  -serial chardev:serial
  -drive if=pflash,format=raw,readonly=on,file=<private-OVMF-CODE>
  -drive if=pflash,format=raw,file=<run>/OVMF-VARS.fd
  -drive if=virtio,format=qcow2,file=<run>/overlay.qcow2,cache=none
  -drive file=<run>/seed.iso,format=raw,media=cdrom,readonly=on
  <明确接线的只读payload设备>
```

参数需按临时提取的 **8.2.2 Ubuntu build** 实際 `--help`/设备能力确认；本次访问的
在线 master 文档版本较新，不能引入仅 9.x/11.x 才有的命令。guest panic 通知另加
已验证的 pvpanic 设备/driver，不以 device 参数存在代替 guest 驱动有效。[Q1][Q3]

`-nic none` 显式取消默认用户网络；无 hostfwd、TAP、bridge、共享端口、外部控制服务。
全部管理经 0700 目录内 Unix socket；设置 umask 077，QMP 不监听 TCP。serial 与 QMP
分离，不用 `-nographic` 默认 multiplex 混淆机器证据。可以用 QMP `-S`/`cont` 在准备
阶段停住 vCPU，但这种 pause 不能作为 crash 证据。

KVM：ACL 允许不等于初始化成功；失败时可以选明确 TCG 配置，不能静默降级后沿用
KVM 性能阈值。TCG 是软件仿真，能支持故障语义校准，但慢到超出固定墙钟时应停止，
不抬预算。不要修改宿主 module、CPU governor、groups 或 `/dev/kvm` 权限。[Q1]

## 5. 故障模型必须分开报告

| 动作 | 可证明的故障 | 不能声称 |
|---|---|---|
| QMP `system_reset` | 指定 guest 的硬件重置/重新启动；应有 QMP RESET 与新 guest boot_id | 已触发 guest kernel panic；宿主掉电；存储设备缓存丢失 |
| guest 内真实 kernel panic | guest kernel 停止正常执行；serial panic 信息，配置好 pvpanic 时有 GUEST_PANICKED | 宿主 panic；真实硬件 power-loss；QEMU 进程已被杀 |
| 精确 owned QEMU `SIGKILL` | guest RAM 与 emulator 进程突然消失，無 QEMU 正常退出清理；同 overlay 重新启动 | 宿主页缓存丢失/块设备电源丢失；所有已 ack write 都 durable |
| QMP `system_powerdown` / guest shutdown | ACPI/guest 正常关机路径 | crash 或掉电 |
| QMP `quit` / QEMU SIGTERM | QEMU 有机会清理的终止路径 | 等价 SIGKILL 或物理掉电 |
| 物理宿主掉电/宿主 OS crash | 本轮完全不实施 | 用上面任一动作冒充完整物理掉电验证 |

QMP run-state schema 区分 host-qmp-system-reset、guest-reset、guest-panic 等原因；
命令回复 `{return:{}}` 不等于 guest 已完成恢复，必须看事件、boot identity 和业务证据。
pvpanic 是 guest 通知 QEMU 的设备，未装/未加载 guest driver 时缺少事件并不自动证明
没有 panic。[Q3][Q4]

guest panic 应只通过私有来宾内、匹配 instance nonce/boot_id 的专用夹具触发；
如果使用 Linux SysRq crash（guest 内 `/proc/sysrq-trigger` 的 `c`），这条操作必须
限定于**来宾控制通道**和来宾 root，绝不能作为宿主 shell 命令运行。不得执行宿主
sysrq/reboot/kexec，也不允许 privileged Docker 代替隔离 VM。来宾 panic 是否自动
重启、是否执行 kdump、QEMU panic action 都需预先固定并记录，避免“通知后自动
重启”被误当无 panic。`inject-nmi` 本身也不保证导致 panic，不能代替已验证夹具。

SIGKILL 路线必须由 supervisor 直接创建并持有 QEMU 子进程 Popen/等价句柄，记录
PID、启动身份、QMP greeting/version、镜像/run nonce，再给**仍存活的同一子进程**
发信号并 wait；不得从残留 PID 文件或 `pgrep qemu` 杀进程。清理仅处理 owned 子进程、
Unix sockets 和本次路径，不能 `killall qemu/tmux`。

## 6. cache 模式与持久化证明边界

QEMU cache 表明确：[Q1]

- `writeback`：guest write-cache=on、host direct=off、no-flush=off。
- `none`：guest write-cache=on、host direct=on、no-flush=off。
- `unsafe`：no-flush=on，**禁止用于可信持久化验收**。

因此 `cache=none` 是绕过宿主页缓存的请求，并**不**表示没有 guest/device/controller
缓存，也不自动把每次业务 write 当成 durable。`writeback` 下杀 QEMU 时宿主仍在、
页缓存可能继续写回，尤其不能称“物理断电”。两者均应尊重正常 guest flush，但物理
设备是否真的把数据写到非易失介质仍在本隔离模拟的证明边界外。`cache=none` 不被
临时 filesystem 支持时应失败，不静默改成 writeback；若后续选择 writeback，要单列
故障模型与结果，不在同一通过声明中混用。

故障窗口要锚定 ZaoFu 正式写入/append/fsync/atomic-rename 合同的事实与摘要，不靠
测试额外 `sync` 把临界窗口变成干净快照。至少区分已确认 durable 前缀与允许丢失的
在途尾部；追加一条“测试 marker 已打印”不能证明其之前所有状态已刷盘。

## 7. 最小实施骨干与成功证据

1. **工具/镜像来源 → verify：**精确包/镜像/固件/载荷 manifest+哈希；所有解析路径
   在临时树或明确已登记宿主只读共享库内；无宿主配置/网络变化。
2. **无故障来宾启动 → verify：**限定 RAM/vCPU，无 NIC；QMP handshake/capabilities、
   version/schema、serial 实例 nonce、guest boot_id、代码/解释器 SHA 与 source-bound
   预期一致；离线 ZaoFu 依赖与 tmux 入口可用。只证明前置，不算 OS crash。
3. **正式持久状态窗口 → verify：**guest 内用真实配置/state_dir、正式 runtime/CLI
   与 mock provider 运行一个在途 Run；证据同时记录 Task/Run/attempt/operation、
   canonical ledger 前缀摘要和允许的临界窗口。不得用直接写 kanban 模拟正常准入。
4. **单一故障 → verify：**先选择 owned emulator abrupt termination 作为独立故障
   模型，或明确 guest panic；保留外部时间、QMP/serial 和进程终止事实。每个模型
   单独结论，不把 reset 一次当成覆盖所有 crash 类型。
5. **同磁盘 takeover → verify：**重新启动相同 overlay/配置，新的 guest boot_id，
   通过正式恢复入口继续原 Run；核对已确认前缀不变、事件/sidecar ref一致、Task
   状态和 attempt/operation 正确、旧结果不重复准入、外部受控副作用无重复，最终
   交付/owner projection 证据完整。具体业务合同须先从当前测试/运行时定向调研。
6. **终止/清理 → verify：**正常清理仅发生在故障/恢复证据冻结之后；owned QEMU
   已退出，socket无listener，base SHA不变，无运行中的磁盘再被 qemu-img 修改；
   保留有界证据/失败包。不能为了清理抹除唯一失败 overlay/原始串口日志。

## 8. 建议预算与失败停止条件

先按 **1 vCPU/1GiB guest RAM** 做启动校准；最多 2 vCPU/2GiB guest RAM，不加 guest
swap。guest RAM 上限不是 QEMU 总 RSS 上限，supervisor 还需记录 QEMU RSS/CPU、
宿主 MemAvailable/swap 活动与磁盘；建议进程 RSS 上限 3GiB、新增总磁盘 8GiB、宿主
剩余磁盘至少 12GiB、MemAvailable 至少 6GiB。阈值需在启动前固定，不边跑边抬。

建议每次启动 180 秒、恢复 180 秒、单一模型整轮 600 秒、清理宽限 30 秒；TCG
不达标就记资源/能力阻断，不扩大测试业务预算或循环重启求绿。首轮不并发 VM。
现有 swap 高占用不自动等于当前 swap thrashing，但若观测到持续 swap-in/out、
MemAvailable 快降或共享宿主响应受影响，应立即终止自有 VM 并记录。

下列任一项立即 fail-closed：来源/签名失败；包依赖不闭合；要靠 sudo/宿主 mount、
bridge、裸盘、可写工作区、全局安装才能继续；QEMU 的磁盘/固件/backing 路径越界；
guest identity 不匹配；QMP/serial 无法证明目标；资源或墙钟超限；guest 无法离线
运行真实 ZaoFu 入口；故障注入只得到正常 shutdown；恢复改变已确认 durable 前缀、
出现重复副作用或证据缺失。先保留原始失败，再决定是否有新的安全方法，不补造通过。

## 9. 已访问的一手资料

- [A1] Debian `apt-get(8)`（download、固定版本、下载与安装区别）：
  <https://manpages.debian.org/bookworm/apt/apt-get.8.en.html>
- [A2] Debian `dpkg-deb(1)`（extract 只提取，不构成安装）：
  <https://manpages.debian.org/bookworm/dpkg/dpkg-deb.1.en.html>
- [U1] Canonical noble cloud image 索引：<https://cloud-images.ubuntu.com/noble/current/>
- [U2] Canonical checksum/signature 验证：
  <https://documentation.ubuntu.com/public-images/public-images-how-to/verify-image-checksum/>
- [U3] Canonical 镜像包清单：
  <https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.manifest>
- [C1] cloud-init NoCloud datasource（CIDATA、本地 ISO、overlay 示例）：
  <https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html>
- [Q1] QEMU invocation（TCG/KVM、cache 表、-nic none、QMP/serial、-L、资源）：
  <https://www.qemu.org/docs/master/system/invocation.html>
- [Q2] QEMU disk images（snapshot 模式与 qcow2 backing）：
  <https://www.qemu.org/docs/master/system/images.html>
- [Q3] QEMU pvpanic 设备：<https://www.qemu.org/docs/master/specs/pvpanic.html>
- [Q4] QEMU QMP reference 与 8.2.2 run-state schema：
  <https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html>；
  <https://github.com/qemu/qemu/blob/v8.2.2/qapi/run-state.json>

网络可用，已访问上述小型文档/manifest；大型 `.img`/`.deb` 均未下载。本轮只完成
K571 方法与安全边界调研，所有启动、故障、接管和业务验收仍为待执行。

## 10. 后续执行校准（2026-09-25，主线程追加）

上文“未下载/未启动”描述调研截止点；随后执行结果以steps K571-A/B为准。
实际选用更小的官方Minimal镜像而非§3的server镜像：
<https://cloud-images.ubuntu.com/minimal/releases/noble/release-20260905/>，
`ubuntu-24.04-minimal-cloudimg-amd64.img` 264372224bytes，SHA256
`46b0dbaffa6950a7da5ff2dc5ed34c46084610b3b6d1fae8f1ec2d7e953984a3`。
签名清单按Canonical官方fingerprint验证，临时keyring没有修改用户长期keyring。

已验证：固定20包临时提取、QEMU8.2.2/KVM/QMP、1vCPU/1536MiB、无网双启动。
实际预算在启动前固定为新增磁盘6GiB、QEMU RSS3GiB、宿主可用内存6GiB、剩余磁盘
12GiB、单次启动180秒/整轮600秒；没有采用上文建议的可提高资源上限。
首轮CD-ROM seed没有被Linux枚举，不能据UEFI见到SATA设备推定内核也已识别；
来宾SATA/SR驱动存在，具体未加载根因仍未证明。镜像内cloud-init 26.1的
`ds-identify`/`DataSourceNoCloud`按blkid类型/label识别，不要求光驱：

- <https://raw.githubusercontent.com/canonical/cloud-init/26.1/tools/ds-identify>
- <https://raw.githubusercontent.com/canonical/cloud-init/26.1/cloudinit/sources/DataSourceNoCloud.py>

最小可执行调整为seed挂`-drive if=virtio,format=raw,readonly=on,file=...`，保留
CIDATA ISO与NoCloud元数据；最终来宾读到nonce，两次只有`lo`。脚本额外显式将标记写
到ttyS0只是观测通道；调研确认cloud-init正常日志本也进入console，不把它当根因修复。
来宾无NIC仍经历约120秒networkd等待，最后两次均在原180秒预算内；后续若调整离线
network配置，先查cloud-init官方方法，不开启网络或扩大预算。

校准使用显式sync后的测试计数：owned QEMU SIGKILL -9后同overlay启动，新boot_id且
count=2。它证明执行器/私有磁盘可用，**不证明ZaoFu在途数据的耐久或恢复**。
真实测试需要避免照搬这条sync到故障前来造绿。定向代码审查已见EventLog.append
未调用fsync，atomic_write_text只fsync文件再replace；业务故障窗口需要单独设计和实测，
不能从文件当前可读或marker先后推定OS崩溃后都持久。

报告根`/tmp/zf-k571-vm-20260925-WGNTXb/`，基础校准
`calibration3-report.json` pass/260.730324s；前置与两次QEMU均退出，私有gpg-agent已停。
首轮监督器SIGTERM没有自动finally的夹具缺口F3保持open；第二次因uv未绑定项目缺yaml，
第三次显式`--project /home/uisee/zaofu_v7`修正。完整失败记录及报告SHA见steps/Issue。
正式执行器需要先补监督器失联/中断清理，再接离线payload和ZaoFu运行；没有提交/merge/push。
