AC-TXN: transfer 扣源库存、加目标库存、写 ledger 必须全部成功或全部无效。
AC-IDEM: 相同 key 和相同 payload 重试应返回同一 id 且不重复变更；不同 payload 应冲突且零副作用。
AC-AUTH: actor 不在 allowed 时拒绝，库存和 ledger 不变。
范围只含 transfer.py 与 tests/test_transfer.py。不能修改 AC 来使现状符合要求。
