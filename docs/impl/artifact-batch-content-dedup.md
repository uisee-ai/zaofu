# 必读批量输入的同源正文去重

`zf artifact read --attempt <id> --required --deduplicate-content` 保留所有
kernel-required 来源读取，仅在同一次 CLI 输出内共享完全相同的选区正文。
旧 `--required` 输出及单来源读取保持不变；新 flag 必须与 `--required` 同用。

CLI 对每个来源继续调用 `read_attempt_artifact`，执行 capability、access_scope、
allowed_paths、磁盘 SHA-256 校验并追加自己的 read ledger 行。输出 helper
只接收已成功读取的结果，不缓存权限、磁盘读取或收据，不修改 required policy。

新 JSONL 行使用 `artifact-required-read-output.v1`：首次选区包含 `content_id`
与完整 `content`；同源 alias 包含指向同一次输出前文的 `content_ref`。
每行仍含 `source_id`、`artifact_id`、`json_path` 及紧凑 `read_receipt` 定位：
`attempt_id`、`occurrence_id`、`row_sha256`。完整收据仍原样写入该 attempt
目录的 `read-ledger.active.jsonl`，封存后位于 `read-ledger-*.jsonl`；SHA-256
对应每条 ledger 行的 UTF-8 字节，**不包含末尾换行**（ensure_ascii=False、
sort_keys=True、separators=(",", ":")）。可按 attempt 目录和行 SHA 定位
来源 occurrence、consumer 身份与摘要；不重复输出整份收据消耗上下文。
`returned_bytes` 表示该来源绑定的完整
选区正文大小，alias 的正文由同输出先前行提供，不表示省略必读来源。

分组键为规范化实际文件路径、完整 artifact SHA-256、精确 JSONPath、
max_items/max_chars 与实际返回正文 SHA-256。相同正文但不同文件不合并，
截断或不完整摘要不复用。所有 alias 仍独立校验，不能绕过后续拒绝或篡改。

Task Pipeline 和 fanout 共用 briefing renderer，优先推荐新批量命令并保留
逐来源 fallback。若工具截断了正文或缺少引用目标，必须执行该来源的精确
读取命令。新的调用/fresh session 必须重新提供正文，不能用旧 receipt 代替。
现有 context delivery 的 unchanged/delta 仍仅为 shadow，不因此获得免读权限。

本变更不能消除大型 shared 语义材料，只减少机械重复；不用删字段或提高
context hard cap 掩盖输入过大。实测结果和测试记录见
[执行任务](../../tasks/active/2026-11-07-0334-artifact-batch-content-dedup.md)。
