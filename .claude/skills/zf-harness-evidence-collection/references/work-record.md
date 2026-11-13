# 工作记录正文与附件

WRC 是可选的非终态进展记录，不是 Task 完成、独立验收结论或执行授权。
默认继续使用当前 briefing 的已有授权笔记/result scratch；已有合格 event/ref
可以直接按原结果合同引用，不必为了交付额外 publish WRC。

## 发布前区分两个文件

1. 从当前 briefing 核对 operation、attempt、pinned workdir 和已有写入范围。
   `--file` 的正文及其附件都必须是该工作区内已有授权的普通文件。项目根 OUT、
   外部 scratch 或 provider transcript 不会因可读就自动成为合法 publish 路径。
   没有合适的写入位置时，使用已有授权结果字段引用证据，或报告具体权限缺口；
   不创建新的产品证据目录，不改 guard，不用 symlink/复制文件来扩大权限。
2. `--file` 指 WRC **正文**，根对象恰好包含下面四个字段。原生 CommandExecution、
   测试日志和结果报告是**附件**，不能直接作为正文 publish；也不要把 WRC 正文
   写进要求另一种 schema 的 result scratch。
3. `evidence_files` 是相对 pinned workdir 的路径列表，不是相对正文文件所在目录。
   只引用实际存在且已经获准写入的附件；不使用绝对路径或 `..`，不嵌入凭据。

以下仅示范格式；`tests.log` 必须已存在于当前工作区且获准作为证据保存：

```json
{
  "completed_actions": ["已运行并记录本轮检查，逐项验收尚未完成"],
  "pending": ["核对剩余验收项"],
  "next_step": "将核验后的证据引用填入当前授权结果",
  "evidence_files": ["tests.log"]
}
```

三个列表的元素为非空字符串，每列表最多 16 项，每项最多 1024 字符；
`next_step` 为非空字符串，最多 2048 字符。正文最多 16 KiB，单附件最多
64 KiB。超限不截断原始证据冒充完整记录；优先引用已有持久 refs，或按原证据
合同保存有明确范围的附件，保留其来源、身份、目标版本、命令和退出码。

```text
zf artifact work-record publish --operation <当前 operation> --file <已授权正文路径> --request-id <本记录唯一 ID> --state-dir <配置的 state_dir>
zf artifact work-record show --operation <当前 operation> --state-dir <配置的 state_dir>
```

沿用当前角色的受控提交凭据，不在命令/正文中打印它。命令失败时核对错误指向的
路径或字段并修正同一记录，不把 CLI 拒绝当产品失败、完成收据或成功发布。
成功发布后，同 request-id 必须保持正文、附件和绑定不变；新的进展使用新的 ID。
返回的 WRC ref 是未经独立核验的进展，不替代正式 result validate/submit。
`show` 回读按返回 digest 和 next_page_start 续页，附件可用 `--evidence <索引>`。

## 原生命令证据的定位

先按当前 actor/派发查询有限 hook 索引，再读取选中事件和 raw_output 并校验摘要。
短输出可内联，hook task_id 可能为空；异步初查未命中只说明当前窗口未命中。
保留进程/item 标识，有后续进展再刷新有界索引，不反复全量扫描或重跑已通过检查。
必要时读取当前已绑定、获准访问的 provider completed item，核对 command、cwd、
目标提交、进程/item 身份、退出码及完整输出；ordinal 不等于文本行号。
事件时间不等于独立证明的采集延迟。日志片段不提供退出码时保持 unknown。

给他人证据定位时，一并说明它是原生附件、已有可用 ref 还是 WRC 正文，以及合法
使用路径；不要只给一个外部文件位置让后继猜封装方式。已有 refs 的接纳仍由当前
结果合同和正常准入判断，不能自造通过事件。
