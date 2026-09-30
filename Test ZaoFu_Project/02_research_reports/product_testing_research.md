# 生成产品项目测试实践调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_Product_TestKit.md](../03_plans_and_steps/ZaoFu_Product_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/product_testkit/planner.py`。方法选型供产品按能力采用；核心域含 P-17，Pact/Toxiproxy 不作为所有产品的前置。

> 调研目标：了解业界如何测试一个已经生成、交付或部署的产品项目。重点关注测试框架、测试方法、输入构造、结果判定和证据保存；不讨论模型能力排名、Agent benchmark 或整体质量评分。
>
> 资料原则：优先使用测试工具官方文档、官方仓库、论文原文和第一方工程资料。工具是否适用于 ZaoFu，还要结合当前仓库的实际能力判断。

## 结论

没有一个工具能够独立完成任意产品项目的完整测试。成熟实践通常采用分层组合：

```text
产品契约和业务规则
→ 单元/组件测试
→ 集成和服务契约测试
→ API/CLI 测试
→ 关键用户旅程 E2E
→ 安全、恢复、部署和性能测试
→ 统一证据与门禁
```

共同原则是：

1. 产品项目必须先声明可观察的正确行为；
2. 测试输入来自固定样例、边界生成器、API schema、用户旅程或真实协议；
3. 结果由断言、状态、退出码、阈值和禁止副作用共同判定；
4. 外部依赖尽量使用 hermetic 环境、fake service 或一次性容器；
5. 测试报告必须能够复现失败，而不是只给一个分数；
6. 业务验收测试与模型/Agent 评估是两件事。

## 一、测试分层：测试金字塔

Martin Fowler 的测试金字塔将测试分为 unit、integration、component、contract 和 end-to-end。低层测试数量多、执行快，高层 E2E 数量少但覆盖关键用户旅程。微服务测试实践还强调，应优先测试服务边界和契约，而不是把所有场景都变成昂贵的 UI E2E。

### 测试输入

- 单元：函数参数、边界值、错误值、空值和重复值；
- 集成：数据库、文件系统、队列和 HTTP 协议操作；
- 契约：消费者真实发出的请求/响应 interaction；
- E2E：用户从入口到结果的完整动作序列。

### 结果判定

- 断言结果和预期值一致；
- 预期异常确实发生；
- 依赖通信符合契约；
- 用户旅程的最终状态满足产品验收条件；
- 高层测试不应取代低层测试。

来源：

- [Martin Fowler: Test Pyramid](https://martinfowler.com/bliki/TestPyramid.html)
- [Martin Fowler: Testing Strategies in a Microservice Architecture](https://martinfowler.com/articles/microservice-testing/)
- [Martin Fowler: The Practical Test Pyramid](https://martinfowler.com/articles/practical-test-pyramid.html)

## 二、Hermetic 测试：先固定环境再测试产品

Google 的 Hermetic Servers 方法强调测试环境应尽量不依赖外部网络和开发机上已有服务。

典型流程：

```text
固定代码版本和依赖
→ 创建隔离服务/数据库
→ 执行测试输入
→ 检查产品行为
→ 收集日志和状态
→ 清理所有资源
```

### 输入

- 固定 commit；
- 固定依赖锁文件；
- 独立工作目录、数据库和环境变量；
- fake service 或本地测试容器；
- 明确禁止访问生产服务。

### 判定

- 同样输入在不同机器上得到同样结果；
- 外部服务不可用不会导致无法区分的随机失败；
- 测试结束没有遗留进程、端口、容器和临时数据；
- 失败日志能够区分环境失败与产品失败。

来源：[Google Testing Blog: Hermetic Servers](https://testing.googleblog.com/2012/10/hermetic-servers.html)

## 三、pytest：固定场景、参数化和结构化报告

pytest 的常见模式是使用 fixture 创建隔离环境，用参数化复用同一个测试函数，并通过直接断言或预期异常判定结果。

```python
@pytest.mark.parametrize(
    "input_data, expected",
    [
        (valid_input, expected_output),
        (boundary_input, expected_boundary_result),
        (invalid_input, expected_error),
    ],
)
def test_product_behavior(input_data, expected):
    result = run_product(input_data)
    assert result == expected
```

### 适合生成产品项目的输入

- 命令行参数和 stdin；
- HTTP 请求 body、query、path 和 header；
- 数据库记录和消息；
- 文件、配置和环境变量；
- 登录用户和角色；
- 正常、边界、非法、空和重复场景。

### 结果判定

- 进程退出码；
- Python 断言；
- `pytest.raises` 预期异常；
- 数据库、文件和状态变化；
- 禁止的外部调用次数为 0；
- JUnit XML 中无 failed/error。

来源：

- [pytest fixtures](https://docs.pytest.org/en/stable/explanation/fixtures.html)
- [pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)
- [pytest JUnit XML](https://docs.pytest.org/en/stable/reference/reference.html)

## 四、Hypothesis：生成输入并检查不变量

Hypothesis 用于测试产品代码，不用于评价生成产品的模型。它可以自动生成边界、Unicode、极值、重复值和复杂状态序列，并在失败时缩减为较小的复现输入。

### 典型状态序列

```text
创建资源
→ 修改资源
→ 重复修改
→ 非法输入
→ 删除
→ 恢复
→ 再次读取
```

### 可判定的不变量

- API 不返回未声明的 5xx；
- 重复请求保持幂等；
- 删除后的资源不可见；
- 无效数据不会污染数据库；
- 序列化再反序列化保持等价；
- 状态转换始终满足产品规则。

来源：

- [Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html)
- [Hypothesis stateful API](https://hypothesis.readthedocs.io/en/latest/reference/api.html#hypothesis.stateful.RuleBasedStateMachine)

## 五、Schemathesis：从 OpenAPI/GraphQL 生成 API 测试

Schemathesis 从 OpenAPI 或 GraphQL schema 生成合法和非法请求，也可以根据 operation links 组成多步资源操作。

### 输入来源

- schema 的字段类型和约束；
- required 字段缺失；
- 类型错误；
- 边界数值和字符串；
- schema examples；
- 运行时响应中的资源 ID；
- 认证、未认证和错误 scope。

### 判定方式

- 是否出现 5xx；
- status code 是否符合 schema；
- response content type 和 headers；
- response body 是否符合 schema；
- 非法输入是否被拒绝；
- 多步操作后的资源状态是否正确；
- 失败请求序列是否可以复现。

它可以输出结构化测试结果，但不会自动理解业务语义。例如，不能仅靠 Schemathesis 判断“HTTP 返回成功但数据库状态错误”。关键 API 用例仍要回查产品状态或业务断言。

来源：

- [Schemathesis Documentation](https://schemathesis.readthedocs.io/en/stable/)
- [Schemathesis checks](https://schemathesis.readthedocs.io/en/stable/reference/checks/)
- [Schemathesis stateful testing](https://schemathesis.readthedocs.io/en/stable/guides/stateful-testing/)
- [Schemathesis GitHub](https://github.com/schemathesis/schemathesis)
- 论文：[Deriving Semantics-Aware Fuzzers from Web API Schemas](https://arxiv.org/abs/2112.10328)

## 六、Pact：消费者驱动契约测试

Pact 的流程是：

```text
Consumer test
→ 记录真实 request/response interaction
→ 生成 Pact contract
→ Provider 在测试状态下重放
→ 比较 Provider 响应与 contract
```

### 输入

输入不是 Provider 的所有可能行为，而是消费者真正使用的请求/响应场景，包含请求参数、header、body 和期望响应。

### 判定

- 每个 interaction 都能被 Provider 响应；
- 响应字段、类型和状态码满足契约；
- Provider state 能正确建立；
- 不需要同时启动整个生产服务集群。

Pact 解决的是服务接口共同理解，不替代产品业务功能测试。

来源：

- [Pact Documentation](https://docs.pact.io/)
- [Writing Consumer Tests](https://docs.pact.io/consumer)
- [Provider Verification](https://docs.pact.io/implementation_guides/javascript/docs/provider)
- [How Pact Works](https://docs.pact.io/getting_started/how_pact_works)

## 七、Testcontainers：真实依赖的隔离集成测试

Testcontainers 在测试开始时启动一次性数据库、队列、缓存或其他依赖服务，等待健康后运行测试，结束时回收容器。

```text
启动容器
→ 等待健康
→ 执行真实协议和数据库操作
→ 检查读写、迁移、事务和约束
→ 采集容器日志
→ 删除容器
```

### 输入

- 真实数据库连接和 SQL/ORM 操作；
- 真实消息协议；
- 真实 HTTP/WebSocket 协议；
- migration、备份和恢复操作。

### 判定

- 依赖连接和健康检查成功；
- schema/migration 正确；
- 事务、约束和重复写行为正确；
- 依赖故障时产品以预期方式失败；
- 测试后容器和网络资源被清理。

来源：[Docker Testcontainers](https://docs.docker.com/testcontainers/)

## 八、Playwright：浏览器用户旅程和可观察证据

Playwright Test 为每个测试提供隔离 BrowserContext，使用 locator 和自动重试断言执行真实用户动作。

### 典型输入

```text
打开页面
→ 登录
→ 填写数据
→ 点击提交
→ 等待状态变化
→ 查询或刷新结果
```

### 典型判定

```typescript
await expect(page.getByTestId("status")).toHaveText("Submitted");
await expect(page.getByRole("alert")).toBeHidden();
```

更完整的判定应检查：

```text
点击前状态 = Pending
点击动作
→ API 成功
→ 数据状态改变
→ 页面显示 Submitted
→ 刷新后仍为 Submitted
```

### 证据

- HTML report；
- screenshot；
- trace；
- DOM snapshot；
- network 和 console 日志；
- 退出码。

只检查页面挂载、按钮存在或截图“看起来正常”不能证明产品功能正确。

来源：

- [Playwright fixtures](https://playwright.dev/docs/test-fixtures)
- [Playwright assertions](https://playwright.dev/docs/test-assertions)
- [Playwright running tests](https://playwright.dev/docs/running-tests)
- [Playwright Trace Viewer](https://playwright.dev/docs/trace-viewer)

## 九、k6：用预先声明的阈值测试性能

k6 用流量模型驱动 API/Web/SSE，并用 threshold 把性能预算转换为明确的 pass/fail。

```javascript
export const options = {
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500"],
  },
};
```

### 输入

- 并发用户数；
- 请求速率；
- 运行时间；
- API 请求数据；
- 自定义业务指标；
- spike、stress 和 soak 场景。

### 判定

- 错误率低于预算；
- p95/p99 延迟低于预算；
- 自定义指标满足阈值；
- 阈值不满足时退出码非零。

没有预先阈值时，性能报告只是观测数据，不是测试结论。

来源：[k6 Thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/)

## 十、OWASP ZAP：动态 Web/API 安全门禁

ZAP Baseline Scan 主要做爬取和被动扫描，Automation Framework 可以组合 OpenAPI 导入、认证、爬取、主动扫描和规则级测试。

### 输入

- 经过授权的测试 URL；
- OpenAPI/GraphQL 定义；
- 测试账号；
- 扫描规则和风险阈值。

### 判定

```text
高风险问题 > 0 → fail
中风险问题超过项目允许数量 → fail
未授权目标或生产 URL → harness failure
```

ZAP 不能替代业务权限、角色隔离、事件签名或输入到危险副作用的测试。

来源：

- [ZAP Baseline Scan](https://www.zaproxy.org/docs/docker/baseline-scan/)
- [ZAP Automation Framework](https://www.zaproxy.org/docs/automate/automation-framework/)

## 十一、常见结果证据

业界通常同时保留以下证据，而不是只保存最终分数：

| 证据 | 作用 |
|---|---|
| 退出码 | 机器门禁的最小结果 |
| 断言明细 | 说明哪条产品行为失败 |
| JUnit XML/JSON | CI 和报告聚合 |
| stdout/stderr | 重现命令和错误上下文 |
| 数据库/文件快照 | 证明状态变化或污染 |
| screenshot/trace/HAR | 浏览器和 HTTP 过程证据 |
| 容器/服务日志 | 集成失败定位 |
| 性能 threshold 报告 | 证明预算是否满足 |
| 安全扫描报告 | 证明风险规则结果 |
| commit、依赖和环境快照 | 证明测试对象身份 |

## 十二、适用于 ZaoFu 生成产品的综合方法

对每个通过 ZaoFu 生成的产品项目，建议把上述实践组合成以下固定闭环：

```text
固定产品 commit、依赖和隔离环境
→ 读取产品 test/acceptance manifest
→ 执行 unit/component/integration
→ 执行 API/CLI contract
→ 执行关键 Web/用户旅程 E2E
→ 按产品能力执行数据库、外部集成和安全测试
→ 执行恢复、幂等和故障场景
→ 发布阶段执行性能、部署、升级和回滚
→ 汇总退出码、断言、状态、日志和 trace
→ 根据 mandatory case + forbidden side effect 做最终裁决
```

每个 case 至少包含：

```text
Given       初始代码、环境、数据和身份
When        命令、请求、用户动作或故障注入
Then        必须成立的输出、状态和业务结果
Forbidden   不能出现的状态、日志、文件或外部副作用
Evidence    可复现的报告、日志、快照、trace 和 digest
```

最终通过条件是：

```text
所有 required case 通过
+ 所有 mandatory acceptance 有证据
+ API/CLI/DOM/数据库状态一致
+ 恢复后结果与无故障 baseline 一致
+ 未授权或重复外部副作用为 0
+ 没有仅靠重试变绿、未分类错误或资源泄漏
```

## 十三、与“评估”的边界

以下内容不是本调研的产品正确性测试：

- 比较不同模型的平均成功率；
- benchmark 排名；
- LLM judge 的整体质量分；
- token、成本或响应速度的单独评分；
- 多次运行后统计“看起来更好”的概率。

这些属于模型/Agent 评估或运营分析。对于产品项目测试，必须先有明确的产品契约和可观察 oracle，再用工具稳定执行并给出可复现的 pass/fail。
