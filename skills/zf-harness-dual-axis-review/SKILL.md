# Skill: zf-harness-dual-axis-review

> Sprint: ZF-SKILL-MP-005 (doc 39 §4.7)
> 目标角色: review / judge
> 状态: net-new

## 目的

review/judge 永远沿 **两条轴**评估代码：

1. **Standards 轴** — 代码风格、API 兼容、测试覆盖、性能 / 安全等
   "技术正确性"
2. **Spec 轴** — 是否真的实现了 task.contract.behavior /
   acceptance / spec_ref 要求

两轴必须**分别**给结论。"代码风格正确但需求错"或"需求实现但违反架构
规范"都不能 approve。

## 操作规约

review approval payload 必含两轴结论：

```bash
zf emit review.approved --task <task_id> --actor <instance> \
  --payload '{
    "standards_axis": {
      "verdict": "pass",
      "checks": ["pep8", "type_hints", "test_coverage", "no_new_imports"]
    },
    "spec_axis": {
      "verdict": "pass",
      "acceptance_evidence": [
        {"criterion": "<from task.contract.acceptance>", "evidence_ref": "..."},
      ]
    }
  }'
```

任一轴 `verdict != "pass"` → 必须 emit `review.rejected` 并说明哪一轴。

## 反模式

- ❌ "代码看起来不错" 不分轴
- ❌ Standards 通过就 approve，没核对 acceptance
- ❌ Spec 通过但忽视 architecture rules

## 关联

- ZF-LH-SP-001 State Packet contract.acceptance 是 spec 轴的真值
- `skills/zf-yoke-review-role-context/` 现有 review skill (可叠加加载)
