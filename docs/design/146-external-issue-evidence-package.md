# External Issue 证据包与附件发布方案

> 状态: defer（仅记录方案，暂不实现）

## 背景

当前 `needs_info` 补充卡片可以收集文本、选项和布尔答案，但还没有把用户上传的日志/图片，以及 worker、orchestrator/runtime 和 Playwright 产生的现场证据纳入同一条可审计的发布链路。GitHub Issue API 也没有普通的二进制 Issue 附件上传接口，因此不能把二进制内容直接塞进 Issue 正文。

本方案保留为后续实现项。它不采用“公开证据仓库”作为本阶段实现目标；证据发布位置、可见性和权限策略在实现前仍需单独决策。

## 目标

当用户补充 `needs_info` 时，下一轮 External Issue Triage 能同时获得：

- 用户在 ZaoFu 卡片上传的日志和图片；
- Issue 原始正文、评论中的图片链接及其下载结果；
- worker 收集的日志；
- orchestrator/runtime 诊断日志；
- Playwright 截图、页面录制等现场证据。

所有证据都必须有稳定引用、内容摘要和来源，不直接修改项目源代码或 `dev` 分支。

## 证据包模型

ZaoFu 先把文件保存为不可变 artifact sidecar，并在证据清单中记录：

- 文件名和 MIME 类型；
- SHA-256；
- 证据来源（用户上传、Issue、worker、runtime、Playwright）；
- 所属 Task ID、Triage ID 和 round；
- 是否经过脱敏；
- 文件大小和采集时间；
- sidecar ref 及其内容类型。

证据包应由一个不可变 `manifest.json` 描述。manifest 只引用文件，不复制大段二进制或日志正文。

## 受控发布流程

1. `needs_info` 卡片允许用户选择文件并填写答案；上传内容先写入本地不可变 sidecar。
2. 系统合并用户附件和运行时已采集证据，执行类型、大小、数量和 digest 校验。
3. 通过受控 action 发布证据包到配置的 GitHub 证据位置，建议使用独立的 `triage-evidence/<triage-id>` 分支或专用目录，不能写入项目 `dev` 源码分支。
4. 发布内容包括日志文件、图片文件和 `manifest.json`。
5. 在 Issue Comment 中写入摘要、Triage ID 和 Markdown 链接；同一 marker 重放时必须幂等，不能重复创建评论。
6. Comment 发布成功后，系统刷新 Issue，创建新的 Task/Triage ID，并开始下一轮只读 Triage。

示例评论结构：

```markdown
## ZaoFu Triage clarification

### Logs
[runtime.log](.../artifacts/triage/TRIAGE-.../runtime.log)

### Screenshots
![Playwright screenshot](.../artifacts/triage/TRIAGE-.../playwright.png)

Manifest:
[manifest.json](.../artifacts/triage/TRIAGE-.../manifest.json)
```

## 下一轮 Triage 的输入契约

Issue ingress 不能只读取正文和评论文本。它还需要：

1. 解析评论中的证据链接和 manifest；
2. 通过 GitHub provider 下载证据文件；
3. 将下载内容重新写入本地 artifact catalog，并校验 manifest 中的 digest；
4. 在 External Issue source snapshot 和 workflow input manifest 中同时提供 `body_ref`、`comments_ref`、`attachment_refs`；
5. 让 worker 通过受控 artifact 读取路径访问这些内容，而不是依赖本地绝对路径或 ZaoFu Web 会话。

下载失败、digest 不匹配或链接已失效时必须 fail-closed，并将具体证据缺口反馈为新的 `needs_info`，不得把未校验的远程内容当作事实输入。

## 安全与边界

- 证据发布必须经过受控 action，集成层不能直接写 canonical Task、Issue 或 Event 状态。
- 日志发布前需要脱敏 token、密码、Cookie、Authorization Header 等敏感值。
- 限制允许的 MIME 类型、单文件大小、总大小和文件数量。
- 原始证据保留 digest 和来源，任何替换都应生成新的证据包，而不是覆盖旧 sidecar。
- 证据文件是否公开、是否允许私有 Issue 使用、保留周期和访问令牌范围，在实现前必须形成明确配置与人工确认策略。
- 该方案不暴露整个 ZaoFu Web，不把 `.zf` 或本地工作树直接作为下载目录。

## 暂不实现的部分

本设计暂不修改：

- `needs_info` 卡片的文件选择 UI；
- GitHub 二进制文件上传 API；
- 公开 URL、GitHub Pages、对象存储或其他外部托管；
- Issue Comment 的附件链接解析和下载；
- worker 输入 manifest 的 `attachment_refs` 扩展。

## 实现前需要决策

1. 证据发布到现有 Issue 仓库的专用目录，还是独立证据仓库/对象存储。
2. 证据的默认可见性、私有 Issue 的处理方式和人工确认点。
3. 文件大小、类型、保留周期及脱敏失败时的阻断策略。
4. Issue Comment 无写权限时，如何把 manifest URL 传递给下一轮 Triage。
