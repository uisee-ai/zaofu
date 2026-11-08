# 大段必读输入的分页方法说明

> 状态: done — a0bffe7e docs: clarify bounded artifact pagination methods

用户授权：持续监工LS-v1，发现问题修复并记录。本项属于现有工具用法纠错，不变更任务验收。

现场证据：FMS16 Verify曾请求32768，FMS17 Verify2请求过大page-bytes，FMS13 Verify1于
2026-11-08 19:20尝试30000，CLI均正确拒绝。现有说明只解释默认字节预算及缩小方法，
没有明确范围或大段中文可切换既有字符分页。根因是角色方法不完整，不是授权/读取门禁错误。

验收：

- 更新Impl/Verify方法 -> verify: 说明1024—8192字节上限、字符分页8192、来源/连续位置、丢失补读，保留完整必读和授权。
- 同步分发 -> verify: 两角色canonical与Codex/Claude分发字节一致，元数据校验通过。
- 核验既有读取行为 -> verify: 精确CLI、批量分页、必读页面相关现有行为测试及指令检查通过；不增加镜像文本测试。
- 合入并投递LS项目技能差量 -> verify: 保留项目已有差异，记录前后摘要；在途worker一次方法指导、新派发实际技能摘要可核验。

范围：skills与实现说明。无Python/runtime逻辑改动、不扩CLI数值范围、不减少required_reads，
不因方法更新重启控制面或重派任务。不把历史读取覆盖推为当前记忆，也不保证worker一定选择某种分页。

验证：既有读取CLI/分页/指令检查35 passed；dev-verify自动闭包41 passed、1 skipped；
预合并哨兵53 passed；两个技能元数据有效且六份canonical/distribution文件一致。
未跑全量pytest或真实provider E2E。现场项目差量部署和在途方法投递在合并后记录。

交付：a0bffe7e合入dev c42f2bef；LS项目只应用本次差量、保留既有技能差异。
FMS03 Impl与FMS13 Verify已读取正式方法反馈并采用精确分页。19:39:07新任务
FMS-DB-READINESS-01实际派发evt-6f30ba2e30a9，其dev-lane-1材料化技能
SHA256 df6af9e524dc7b5cb65ee2c9f4b0c26ef67d6bc39af4b58f8516947afb96295b匹配项目源。
合并后24 passed、1 skipped。记录见LS docs/records/2026-11-08-read-page-method/。
没有Python逻辑变更、没有控制面重启或既有attempt重派；不把方法投递视为理解或完整性能保证。
