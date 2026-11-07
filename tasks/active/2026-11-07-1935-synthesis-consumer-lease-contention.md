# Workflow Synthesis consumer 锁竞争分类

> 状态: active

持续监工获授权修复卡点。现场evt-92c8b53fa5d8（19:14:53）及19:31:27重复出现
workflow-synthesis-consumer.guard锁0.1秒获取超时；锁随后释放，任务继续。
Dashboard准备worker将此视作另一host正在消费并稍后重试；运行时tick却发
orchestrator.tick.failed，制造失败信号。不可通过吞掉所有TimeoutError掩盖真实处理错误，
也不能返回0导致Web把未成功获取lease的输入fingerprint标为seen。

- 复现真实file lock竞争 -> verify: RED runtime错误发失败，guard正文未进入且事件不变。
- 仅将获取consumer lease的Timeout包装为专用Busy -> verify: runtime不发失败、Web保持未seen可重试；释放锁后可运行。
- 保留lease内真实读/处理超时 -> verify: 原TimeoutError仍产生tick failure，不无限静默。
- 覆盖运行时tick、CLI与Web调用者 -> verify: 聚焦影响闭包、sentinel与相关mock E2E通过；现场保留两个活动Impl身份。

实现不改业务状态、任务合同、全局锁等待或provider超时，不重写历史错误事件。

验证：真实锁竞争测试先2RED/2GREEN，修复后聚焦调用链79通过；
`dev-verify.py plan/run --base HEAD`以当前补丁前提交为基线、显式六测试文件，
自动闭包31文件524通过/7跳过，sentinel50通过/1跳过，flow mock10通过。
未运行全量pytest或真实provider。新增异常仅包装enter_context获取lease失败，
lease内TimeoutError仍原样进入tick失败，Web不会因busy提前标记fingerprint。
源码实机部署及保worker重载仍待完成，暂不标done。
