# 保留含中文证据路径的部分成果续作

> 状态: active

授权：用户要求持续监工、记录并修复 bug 后恢复 datal。限定修复 Git 路径输出的机械解析，不修改语义 scope 或绕过已准入失败、独立 Verify、候选 CAS。

## 复现

Privacy R7 部分成果 `9352b324` 的98个改动实际都在授权目录内；默认 Git `diff-tree --name-only` 把13个中文浏览器证据路径输出为 C 风格引号/转义，`partial_successor_proof` 按行切分后误判越界。候选 replay 的 `_commit_files` 有同样问题，`_paths_existing_at_commit` 也使用展示格式。

## 实施与验收

- Git 路径列表使用 NUL 分隔和原始字节解码 -> verify: 中文、引号、制表符、换行、回车、首尾空格名称保持完整。
- 部分成果证明与候选重放使用真实路径 -> verify: 新工作区包含部分成果和候选已集成改动；原候选引用不变。
- 权限边界保持 -> verify: 不在声明 scope 内仍拒绝，缺准入/冲突等原测试保持。
- 记录、局部提交并部署后继续受控恢复 -> verify: 不直接修改 TaskStore、账本、原工作区元数据。

## 验证

新增6例在当前dev复现失败；NUL修复后6例通过。续作/候选集成影响闭包167例通过，sentinel 53例通过，mock E2E 10例通过。初次环境缺少python命令导致1例失败，在未修改dev也复现，加入已有虚拟环境PATH后原样通过。真实9352b324复核：98路径完整，scope误拒绝13→0；未改canonical状态。

`dev-verify.py plan --base dev --tests ...` 因 `candidates.py` 大模块映射选到214个测试文件并升级全量确定性测试。依据仓库按影响闭包验证规则，本次仅改变三个路径读取点与两处Git输出保留，不改Event/Store协议；显式运行相关续作、Candidate和集成调用者测试，自动tiers保留sentinel/mock E2E，跳过无关全量及全局serial集合。不会将focused宣称full验证。

实现说明：[task recovery](../../docs/impl/task-recovery-20261105.md)。
