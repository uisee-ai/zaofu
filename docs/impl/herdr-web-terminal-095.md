# Herdr 0.9.5 Web Terminal 集成规格

基线为 ZaoFu dev 的 4d88585e。用户可以在浏览器里看到哪个 agent 正等待操作，点击支持鼠标的 TUI，在旁观连接中断后回到原终端，并查看当前 Codex 会话的用量。继续兼容通过现有基础探测的 0.8.x 宿主。

| 条款 | 操作与验收 |
|---|---|
| WT1 | 对活跃会话执行 reconcile。Herdr 报告服务未运行、协议不匹配或查询失败时保留 registry；确认 tab 缺失时标记 missing。创建时仅在确认服务未运行后启动 server。 |
| WT2 | 旁观流未发关闭记录便 EOF 时，浏览器重新申请 Observe 并消费完整首帧，其他控制连接继续工作。主动离开和显式关闭沿用正常关闭语义。 |
| WT3 | 在支持结构化鼠标的 Control 中单击 TUI，按同一视口的零基坐标发出左键 down/up。保留文本拖选，Observe 和旧宿主仅使用既有输入能力。 |
| WT4 | 打开或刷新 Drawer，按项目读取 Herdr agent 列表，以 terminal、provider 和 agent 名称匹配。标签展示 working、blocked、idle；探测失败时仍可使用会话。状态仅供展示。 |
| WT5 | Codex 上报原生会话标识时，在校验宿主来源、agent 身份及项目 transcript 后优先用该会话计量。同目录并发会话的计数保持隔离。缺失 hook 时兼容已有发现逻辑。 |
| WT6 | 重新附着使用已记录的 terminal_id。终端输出继续通过有界的 NDJSON/WebSocket 桥传输。 |

验证覆盖后端边界、前端构建，以及 Docker Playwright 通过实际 HTTP/WebSocket 网关执行的用户操作。测试后端可控地注入故障和并发 transcript，真实 Herdr 的结果另行记录。

## 生效条件

沿用 dev 的安装策略和最低宿主版本。启动或刷新 Dashboard 时按宿主能力开放鼠标，列表刷新时更新 agent 标签。原生 ID 来自 Herdr 的 Codex 集成。若先收到 ID，后生成 transcript，保留 ID 直到精确匹配。已有绑定与首次原生 ID 冲突时，该终端的用量显示为不可用。宿主 hook 的安装与检查见[使用手册](../manual/24-web-terminal.md)。

### Codex 历史提问的主题

Herdr 0.9.5 的 NDJSON 桥输出带有显式 RGB 的 ANSI 帧。浅色 Dashboard 根据 Codex 提问标记及其连续底色识别历史提问，使用工作台的浅色底和正文色绘制提问、续行和留白。切换主题或重新附着时，浏览器会重绘已有内容。深色主题使用终端原色，拖选文字使用 xterm 的选区色。

## 浏览器验收与复跑

在本工作树根目录准备依赖和 Dashboard 静态资源。真实宿主档需要已安装的本机 Codex CLI 和官方 Herdr 0.9.5 二进制。Linux x86_64 的 SHA256 为 `14d9e8bc8b4a9e0118d77c32266f76d461f09b40342eb23752960bd89f59929d`。

```bash
uv sync --frozen --extra dev --extra web --extra controller
source .venv/bin/activate
npm --prefix web ci
npm --prefix web run build
bash tests/e2e/scripts/run_web_terminal_herdr.sh fixture
bash tests/e2e/scripts/run_web_terminal_herdr.sh legacy
bash tests/e2e/scripts/run_web_terminal_herdr.sh host /absolute/path/to/herdr
python scripts/dev-verify.py run --base dev --tests tests/test_install_herdr.py
```

三档均在 Docker 镜像 `mcp/playwright:latest` 中运行 Chromium，连接以临时项目启动的真实 Dashboard API 和 WebSocket。每档单独分配端口、运行目录和 action token。

| 档位 | 可观察的验收依据 |
|---|---|
| fixture，0.9.5 协议替身 | 同一目录放入两份 transcript，目标计数为 83，另一份为 916。页面显示 Needs input 和 Working。首格点击回显 down/up 及坐标，拖选和 Shift 点击保留本地行为。第二个页面的旁观流 EOF 后重新取得全帧，原控制页仍可输入。协议错误时会话保持 active，明确缺失后转为 missing。 |
| legacy，0.8.0 协议替身 | 既有 Web Terminal 用户路径覆盖输入、IME、滚动、多标签切换、重连、重命名和显式停止。 |
| host，官方 0.9.5 和本机 Codex | 从页面创建 Codex，读取真实 agent 状态，在同一 session 上依次收到 Control、Observe、Take over 的 PTY 完整帧，随后 reconcile 并停止 CLI。本档停在交互界面，原生 hook 归属及计数由 fixture 档验证。 |

2026-11-23 验收记录：fixture、legacy 和 host 三档各通过 1 条 Chromium 用户路径。后端影响范围覆盖 11 个模块、309 项测试通过；前端类型检查、单元测试、生产构建和 bundle 预算检查通过。
