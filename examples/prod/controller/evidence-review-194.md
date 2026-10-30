# 194 资料核验示例

使用现有 [general-workflow-v3.yaml](general-workflow-v3.yaml) 的
`evidence-synthesis-v1`：scope → 两路采集 → 综合 → 独立验证。
业务变化通过 Requirement 和 skill 表达，不增加业务专属调度器。

在隔离项目中将该配置及其 `common/profiles.yaml` 依赖按原目录关系安装为
`zf.yaml`；五个 `general-*` 角色已配置 `zf-evidence-synthesis`。
将仓库 `skills/zf-evidence-synthesis/` 安装到项目的 skill 来源；其他既有 skill
继续使用配置中的来源。配置仍以该项目的 `zf.yaml` 为唯一控制面。
通过正常 Preparation 确认 Requirement、生成并接纳 Proposal 后启动；
下面是需求正文示例，不是直接写运行态的命令。

> 核验两份版本化产品资料对于离线使用、数据导出和权限管理的描述是否一致。
> 输入 A、B 使用 source manifest 中的 ref/digest，只读这两份资料，不联网补全。
> 采集 A 负责功能声明与适用条件；采集 B 负责限制、例外及冲突。
> 输出 Markdown 报告，逐项列出结论、来源定位、证据版本和未决问题。
> 三项必需覆盖必须有可追溯证据，或明确标为缺口；不得将缺口写成已支持。
> 独立 verifier 检查覆盖和引用。发现缺口后最多进行一次有新资料依据的补查；
> 无新资料或补查后仍不能确认时保持阻塞并说明需要补充的输入。
> source_write=false；本路线只交付已核验报告，不要求代码 Candidate 或 commit。

```text
已确认需求 + 来源 A/B refs/digests
                    |
                  scope
                 /     \
          collect-a   collect-b
                 \     /
                synthesize
                    |
             independent verify
               /           \
        有具体缺口         证据已接纳
             |                  |
   受控修订 + 有界补查     artifact delivery
             |                  |
      新 generation         Goal Gate
```

验证入口：`tests/test_design194_methods.py` 从这份 YAML 加载真实配置、通过现有
模板编译并检查 role skill materialization；
`tests/e2e/test_generic_workflow_complex_mock_e2e.py` 覆盖 Proposal 接纳、资料读取、
缺口修订、generation 隔离、重启、独立验证与 artifact 终态。
Mock 证明运行机制；模型是否正确核验资料、成本和延迟需单独的 Provider canary。

实施边界与测试结果见 [194 实施评估](../../../docs/impl/194-stage-workflow-implementation.md)。
