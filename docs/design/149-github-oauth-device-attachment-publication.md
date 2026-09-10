# 149 · GitHub OAuth Device Flow 与附件发布

> 状态：accepted
>
> 决策日期：2026-09-10

## 背景与问题

Self-Issue 发布链已经能够把正文和文本证据写入 GitHub Issue，并通过 GitHub CLI
`gh issue comment --attach` 上传图片和视频。但是现有 `device_flow` 使用 GitHub App
client id，得到的是 GitHub App user access token。当前 GitHub CLI 的附件上传接口只接受
OAuth token、classic personal access token 或 fine-grained personal access token，因此该
token 可以创建 Issue 和评论，却不能完成附件上传。

这会造成两个不正确的用户体验：

- 用户完成 Connect 后，Issue 已经创建，但二进制证据没有出现在评论中；
- 页面可能再次要求 Connect，用户误以为发布没有发生，重复确认时存在重复 Issue 风险。

目标是让一次显式授权同时满足 Issue、comment、图片和视频发布，并保持共享凭据、幂等
发布、失败可恢复和最小授权边界。

## 决策

新增 GitHub `oauth_device_flow` 授权模式，使用 GitHub OAuth App Device Flow。现有
`device_flow` 保留为 GitHub App 兼容模式，但不再作为带二进制附件的 Self-Issue 发布
目标推荐配置。

推荐配置：

```yaml
self_issue:
  targets:
    github:
      auth_mode: oauth_device_flow
      oauth_client_id: ${ZF_GITHUB_OAUTH_CLIENT_ID}
      oauth_scope: public_repo
```

约束如下：

- `oauth_device_flow` 的 client id 来自 `ZF_GITHUB_OAUTH_CLIENT_ID`；不需要也不得向 Web
  前端暴露 OAuth App client secret；
- `oauth_scope` 首版只允许 `public_repo` 或 `repo`；公开仓库默认使用 `public_repo`，私有
  仓库必须显式使用 `repo`；
- 原有 `device_flow` 继续使用 `ZF_GITHUB_APP_CLIENT_ID`，保持旧项目兼容，但它不承诺
  GitHub CLI 二进制附件上传能力；
- 配置 loader 对未知模式、未知 scope、缺少 client id，以及与模式不匹配的 scope
  fail-closed。

## 授权与发布流程

### 首次授权

```text
Confirm exact preview
  -> Publish
  -> 服务端发现没有与目标模式兼容的凭据
  -> Connect
  -> GitHub OAuth App Device Flow 显示 user code
  -> 用户在 GitHub 完成授权
  -> 页面轮询授权结果
  -> 服务端校验 token 类型、实际 scopes 和目标仓库写权限
  -> 原子保存凭据
  -> 自动恢复原 publication transaction
  -> 创建或复用唯一 GitHub Issue
  -> 发布文本 evidence comments
  -> 上传图片和视频 comments
  -> 显示 Published 与每项证据结果
```

授权完成后不要求用户切换 Draft/Preview，也不要求再次点击 Confirm。刷新或重新打开页面
时，前端从服务端 transaction 恢复真实状态，而不是从本地按钮状态推断发布是否完成。

### 凭据兼容性

凭据存在不等于凭据可用。服务端在任何 GitHub 写副作用发生前，根据当前 target 校验：

- provider、host、用户和 workspace 身份匹配；
- credential 的授权模式与 target 要求兼容；
- token 类型受当前发布能力支持；
- 实际授权 scope 覆盖配置所需 scope；`repo` 可以满足 `public_repo`，反向不成立；
- token 对目标仓库具有创建 Issue/comment 所需的写权限；
- 当 evidence manifest 含图片或视频时，token 同时满足 CLI 附件上传约束。

token 内容和可识别 token 前缀只允许在凭据边界内部判断，不得写入日志、event、sidecar、
错误信息或 Web projection。

若已有共享凭据是旧 `device_flow` 凭据或 scope 不足，服务端返回
`authorization_required`，并说明需要升级授权；不得先创建 Issue 再发现附件无法上传。
旧凭据在新授权成功前保留，避免授权中断破坏其他仍使用旧模式的目标。新凭据通过现有
credential store 的原子写路径替换或新增兼容记录。

### 跨项目共享

GitHub credential 继续以用户、workspace 和 host 为共享边界，同一 workspace 内不同产品
项目不重复授权。每个项目的 target 可以要求不同 scope；复用前必须再次做 target 兼容性
校验。因此“授权一次”表示兼容范围内复用，不表示低权限凭据自动获得高权限。

## 事务身份与幂等

Preview/confirmation 必须绑定以下授权快照：

- provider 与 host；
- repository；
- `auth_mode`；
- 所需 `oauth_scope`；
- evidence manifest digest。

target 的 auth mode、scope、repository 或 evidence manifest 发生变化时，旧 confirmation
变为 stale，必须重新确认。凭据本身的 secret 或刷新时间不进入 preview digest。

publication transaction 是恢复权威。它至少记录 GitHub Issue identity、正文发布状态、
每个 evidence comment 的稳定 marker、每个媒体附件的状态和最后错误。恢复时先读取远端
marker/本地 receipt，再决定补发；不得因为授权轮询、浏览器刷新、页面折叠、Draft/Preview
切换或进程重启而重复创建 Issue。

授权轮询成功后，后端直接恢复同一 transaction。前端即使错过成功响应，也必须通过读取
transaction 得到 `publishing`、`published` 或可恢复失败状态。

## 附件、文本降级与恢复

GitHub CLI 版本必须至少为支持 `gh issue comment --attach` 的版本。启动前检查和实际调用
都返回结构化能力错误，不能把“CLI 太旧”“token 类型不支持”“scope 不足”“仓库无写
权限”混成普通网络失败。

正常路径：

- 文本、脱敏日志摘要和评估结果以带稳定 marker 的 comments 发布；
- 图片和视频使用 GitHub CLI 上传到 Issue comment；
- 每个 artifact 独立记录 `pending`、`published`、`fallback_published` 或
  `retryable_failed`，并绑定 digest，防止同名文件或重试造成重复。

若上传在 Issue 创建后失败：

- Issue 保持唯一，不回滚或重新创建；
- 对失败媒体发布带独立 fallback marker 的文本 comment，说明文件名、类型、digest、失败
  类别和可恢复状态，但不泄露本地绝对路径或凭据；
- 原始文件继续由受控 artifact/sidecar 保存，供同一 transaction 后续 Retry；
- Retry 只补发未成功的 artifact，成功项不重复；
- UI 显示部分成功与明确的 `Reconnect` 或 `Retry attachments` 动作，不得回到初始
  Confirm 循环。

日志和其他不能由 GitHub CLI 作为媒体上传的文件继续按现有受控文本摘要/截断策略进入
evidence comments；本设计不扩大 GitHub 支持的二进制类型。

## Web 状态与文案

Connect 面板说明授权用途：ZaoFu 将代表当前用户创建 GitHub Issue/comment，并上传用户
已确认的图片和视频。页面不得声称所有文件都会作为 GitHub 原始附件上传。

前端使用后端状态机，至少区分：

- `authorization_required`：无兼容凭据；
- `authorizing`：已显示 code，等待 GitHub；
- `resuming_publication`：授权成功，正在恢复原事务；
- `publishing`：正在创建/更新 Issue 与 comments；
- `published`：所有要求项成功；
- `published_with_recoverable_media_failures`：Issue 和文本已成功，部分媒体待重试；
- `failed_before_remote_write`：没有发生 GitHub 写副作用，可安全重试。

浏览器刷新、收起问题卡片或切换 Draft/Preview 不得把上述服务端状态重置为初始按钮状态。
授权 code 过期后可以重新发起一次 flow，但必须继续绑定原 transaction。

## 安全边界

- GitHub 写操作只通过 token-gated、配置白名单约束的 publication service；Web 不直接调用
  GitHub；
- token 只进入现有加密/受保护的 credential store 和调用边界，不进入 runtime state、
  Markdown、命令展示、日志或测试 fixture；
- OAuth 回包中的实际 scopes 必须保存为非秘密元数据，后续复用以它为准，不能使用静态
  `issues:write` 占位；
- repository、host 和 issue URL 继续做 allowlist 与 identity 校验；
- ambient `gh` keyring、用户 shell 登录和其他项目 token 不得被生产发布路径静默采用；
- OAuth App 的注册、client id 配置和 scope 升级是显式管理员操作。

## 兼容与迁移

1. 保留 `device_flow` schema 与运行路径，旧项目不因升级立即失效。
2. 管理员注册 OAuth App，启用 Device Flow，并只配置公开的 client id 到
   `ZF_GITHUB_OAUTH_CLIENT_ID`。
3. 需要附件发布的 target 改为 `oauth_device_flow`，公开仓库配置 `public_repo`。
4. 第一次发布时，旧 GitHub App credential 被判为不兼容并提示一次重新授权。
5. 新授权成功后，同一 workspace 下兼容 target 自动共享；不逐项目重复 Connect。
6. 迁移期间已经创建 Issue 但附件失败的 transaction 使用原 Issue identity 恢复，只补发
   缺失 comments/attachments。

## 实施切片与验证

1. 配置/schema：增加 `oauth_device_flow`、`oauth_scope` 和环境变量解析。
   -> verify：合法组合、未知值、缺失 client id、公开/私有 scope 单元测试。
2. OAuth client：发起带请求 scope 的 Device Flow，轮询后保留 GitHub 返回的实际 scopes。
   -> verify：pending、slow_down、expired、denied、成功和 scope 不足测试。
3. Credential compatibility：在发布前校验 mode、token type、scope 和 repository 权限。
   -> verify：旧 GitHub App token、OAuth token、scope 覆盖、跨项目共享和无远端副作用测试。
4. Publication resume：授权成功后自动恢复同一 transaction，按 marker 幂等创建 Issue、文本
   comment 和媒体 comment。
   -> verify：刷新、Draft/Preview 切换、进程恢复、调用超时和重复回调测试。
5. 附件恢复：结构化识别 CLI/version/auth/upload 错误，发布一次 fallback 并只重试失败媒体。
   -> verify：图片+视频成功、部分失败、全部失败、Retry 后无重复 Issue/comment 测试。
6. Web：展示明确授权用途、自动恢复和部分成功状态。
   -> verify：frontend unit/typecheck/build 与确认—发布—连接—恢复浏览器回归。
7. 运行验证：对隔离的公开测试仓库执行一次显式 real-provider E2E，人工确认 Issue 唯一、
   文本 marker 唯一、图片/视频可见；这属于独立显式 tier，不由 mock 测试替代。

## 验收标准

- 新用户只完成一次 OAuth Device Flow，即可在同一 workspace 的兼容项目中发布；
- Connect 成功后自动继续，不需要返回 Draft 或再次 Confirm；
- 一次 publication transaction 最多创建一个 GitHub Issue；
- 图片和视频正常显示在 comments 中，文本证据同时进入 Triage；
- 授权、刷新、重启或 Retry 不产生重复 Issue、comment 或 attachment；
- 旧 token 或 scope 不足在任何 GitHub 写副作用之前被拦截；
- 上传失败时 Issue 和已成功证据不丢失，失败媒体可见、可重试且原文件仍可追溯；
- token、绝对本地路径和未脱敏内容不进入 Web、event 或 GitHub fallback comment。

## 明确排除

本设计不实现自动 push、PR、merge、Issue 关闭，不使用 ambient GitHub CLI credential，不把
任意二进制伪装成 GitHub 支持的媒体附件，不提供跨 workspace 或跨用户凭据共享，也不把
GitHub OAuth App client secret 下发给浏览器。
