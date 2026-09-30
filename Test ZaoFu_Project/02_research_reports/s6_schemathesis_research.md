# Schemathesis：FastAPI/ASGI OpenAPI pytest 调研（s6）

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s6_schemathesis.py`。已有 ZaoFu API schema 测试，不等于生成产品的 api_contracts 适配器或全部 API 状态序列。

## 范围与版本

本笔记只依据 Schemathesis 官方文档、官方 GitHub 源码/迁移说明和 PyPI 元数据；未安装依赖、未启动 ZaoFu 服务。查询日为 2026-09-11。

- PyPI 当前版本：**4.26.1**；`Requires-Python: >=3.10`，所以 Python 3.11 在支持范围内。[PyPI JSON 元数据](https://pypi.org/pypi/schemathesis/json)
- 官方 Python API 在 v4 将加载器移入命名空间：使用 `schemathesis.openapi.from_asgi`，不要沿用 v3 的顶层 `schemathesis.from_asgi`。[官方迁移说明](https://github.com/schemathesis/schemathesis/blob/master/MIGRATION.md)

## FastAPI ASGI app → OpenAPI → pytest

FastAPI 默认在 `/openapi.json` 提供 OpenAPI；`from_asgi(path, app)` 从 ASGI 应用直接读取该端点，测试请求在进程内执行，不需要监听端口或真实 HTTP 服务。官方最小模式如下：

```python
import schemathesis
from myapp import app

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

`@schema.parametrize()` 为每个 API operation 生成 pytest 参数；`case.call_and_validate()` 发出请求并执行默认检查（包括状态码、服务器错误及响应 schema 一致性）。[Python app 指南](https://schemathesis.readthedocs.io/en/stable/guides/python-apps/) · [Python API：from_asgi/parametrize/call_and_validate](https://schemathesis.readthedocs.io/en/stable/reference/python/)

## 有界 examples 与生成模式

推荐先设小而明确的预算，避免属性测试在 CI 中无界运行：

```python
from hypothesis import settings

@schema.parametrize()
@settings(max_examples=25, deadline=None)
def test_api(case):
    case.call_and_validate()
```

官方教程使用 `@settings(max_examples=500)` 控制每个 operation 的生成数量；较小值适合快速门禁，较大值增加边界发现机会但会延长运行时间。[Pytest 教程（Generating more test cases）](https://schemathesis.readthedocs.io/en/stable/tutorials/pytest/)

Schemathesis 配置文件也支持 `[generation] max-examples = 25`。生成模式由 `generation.mode` 控制：`positive` 仅生成符合 schema 的数据，`negative` 生成违反约束的数据，`all` 两者都生成；官方配置索引同时列出 `generation.no-shrink`、`generation.deterministic` 等边界/复现相关选项。[配置参考：Data Generation](https://schemathesis.readthedocs.io/en/stable/reference/configuration/)

建议拆成两个有界测试（或两个配置运行），使接受与拒绝语义清楚：

```toml
# schemathesis.toml（示意）
[generation]
max-examples = 25
mode = "positive"  # 另一次 negative/all 运行再覆盖拒绝路径
```

## 响应 schema 检查与正/负请求

`case.call_and_validate()` 会将响应与 OpenAPI 中匹配的状态码/响应定义比较；公开检查实现包含 `response_schema_conformance`，其核心是 `case.operation.validate_response(response, case=case)`。[官方 checks 源码](https://github.com/schemathesis/schemathesis/blob/master/src/schemathesis/specs/openapi/checks.py)

负向生成不是“预期任何错误都算通过”：`negative_data_rejection` 会关注负向 case 是否被拒绝，且状态码应符合文档配置的 expected statuses；正向则由 `positive_data_acceptance` 检查接受语义。应在 OpenAPI responses 中明确 4xx/422 等预期结果，并按项目需要配置 `[checks.<check>.expected-statuses]`，否则未文档化的状态码会成为失败。[配置参考：Checks](https://schemathesis.readthedocs.io/en/stable/reference/configuration/) · [官方 checks 源码](https://github.com/schemathesis/schemathesis/blob/master/src/schemathesis/specs/openapi/checks.py)

## 隔离 client 与生命周期

默认 `from_asgi` 会管理 ASGI lifespan；官方说明是加载 schema 时启动应用生命周期，生成请求复用它，解释器退出时关闭。若测试需要自己的 cookie/header/adapter 或必须显式控制生命周期，可使用独立 `ASGIClient` 并传给 `session`：

```python
from schemathesis.python.asgi import ASGIClient

@schema.parametrize()
def test_api(case):
    with ASGIClient(app) as client:
        case.call_and_validate(session=client)
```

这样 client 的 cookie jar、headers 和 adapter 不会依赖外部真实服务；若要跨多个 case 共享会话，应把 client 放入合适的 pytest fixture，并明确其状态重置策略。[官方 Testing Python Apps：Custom Test Clients](https://schemathesis.readthedocs.io/en/stable/guides/python-apps/)

## 失败反例与复现证据

失败时应保留 Schemathesis 输出的 operation、请求方法/URL、headers/body、响应状态/正文及触发的 check。官方教程的失败输出会给出可直接执行的 `curl` 复现命令；这使失败 case 成为可审计反例，而非只保留随机 seed。[官方 Pytest 教程失败示例](https://schemathesis.readthedocs.io/en/stable/tutorials/pytest/)

实务建议：

1. CI 先用固定 `max-examples`；需要稳定重放时保存失败输出中的 cURL/请求 JSON，并结合配置中的 deterministic/seed（若启用）。
2. 不要把负向 case 的任意 5xx 当作通过；未文档化状态码、服务器错误和响应 schema 违约都应失败并进入反例归档。
3. 对认证、数据库或全局状态，在 fixture/client 边界显式隔离；ASGI in-process 测试的优点是没有端口冲突和网络抖动，但应用本身的共享状态仍需测试方清理。

## 对 s6 的落地结论

在 Python 3.11 下可采用 `schemathesis==4.26.1`（或由项目锁文件明确的兼容 4.x 版本），以 `schemathesis.openapi.from_asgi("/openapi.json", app)` 加 `@schema.parametrize()` 生成 pytest 请求；默认调用 `case.call_and_validate()` 取得响应 schema 检查。将 `max_examples` 设为小预算（例如 25），把 positive/negative 分开配置或测试，并使用 `ASGIClient`/fixture 明确 client 与应用状态隔离。失败必须保存 Schemathesis 的最小反例（operation + 请求 + 响应 + check + 可复制 cURL），再进入项目自己的证据/回归流程。
