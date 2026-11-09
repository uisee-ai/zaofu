# R32：重发未改输入的前驱分区继承

> 状态: done — 886ff4c8 fix: retain task input ancestry across explicit republication

授权：继续监工、修复阻塞。真实事件 evt-990b44e7b6e7：Q04新Plan39dac566预检及审批通过，实际Impl交接因无新ID分区延后。原输入ref/SHA91dfc2bd不变，但Planner显式列举后被归为produced；预检可证明祖先，运行时只允许inherited，形成准入/执行不一致。

- 复现真实Handoff入口 -> verify: 原实现拒绝produced重发；新增Impl/Verify及多层用例先红。
- 补充只读祖先证明 -> verify: 当前supersedes包同Run/合同且端口name/ref/SHA/schema一致才建立内存引用，仍要求显式前驱关系。无祖先、跨Run/合同、正文或ref变化均拒绝，不写冻结包。
- 影响闭包及实际部署 -> verify: 入口/输入/包/预检测试、sentinel、mock E2E通过；受控重载保持其他worker，原Q04派发继续且含新小端口和原分区。
- 记录与归档 -> verify: 实现commit、真实receipt、问题/OA/RM边界写入监工记录，成功派发后done，不push。

验证：初始4条正向回归RED、5条拒绝用例PASS；追加伪造source identity/schema漂移后，4文件36通过；最终dev-verify21文件279通过；包/实际查询消费者/多层/任务briefing/replan五文件40通过；sentinel53、flow mock E2E3通过。真实包只读before在Impl/Verify均失败，after均返回原SHA91dfc2bd的FMS21 $.shared+$.tasks[16]与新Q04端口$.shared+$.tasks[0]；冻结包字节不变。尚待实际部署派发，不标done。未跑全量/真实Provider E2E。

实际闭环：dev e81847bf，runtime-load 577a1517e35046d5acab2379c84fe7f4、PID2967129，重载前全部pane/PID保持。12:42:32 evt-0abb7c0c5a25真实派发Q04 Impl，基线最新Candidate f2417b3；attempt ta-b80cde490f5c0b9a6114 的manifest471ad97b同时绑定原FMS21 $.tasks[16]/SHA91df与Q04新端口$.tasks[0]/SHAdf0e。LS证据docs/records/2026-11-07-r32-controlled-reload.json；任务产品实现仍在执行，未将派发当产品完成。
