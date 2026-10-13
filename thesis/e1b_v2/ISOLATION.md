# e1b_v2 与 method_dev 的隔离

本目录继承 [`thesis/e1a_v2/ISOLATION.md`](../e1a_v2/ISOLATION.md) 的全部隔离规则。

核心一句话：**e1b_v2 的 L0 / L1 / L2 / P1 / P2 / P3 / P4 全部必须走 PersonaJsonSurvey 渲染管线，与 method_dev/ 的 run_e1_T_predictions.py 完全无关**。

## 不允许的反向引用

```bash
cd /Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B
grep -rn "method_dev" thesis/e1b_v2 \
    --include="*.py" --include="*.md" \
    | grep -v "隔离" | grep -v "ISOLATION" | grep -v "DEPRECATED"
# 期望：无输出
```

## 共享代码

- PersonaJsonSurvey + HostEnvironment 调用壳代码 → `thesis/e1b_v2/methods/workflow_common.py`（待实现）
- 与 e1a_v2 共用同一 Shell：`thesis/e1a_v2/methods/persona_workflow.py` + `thesis/e1b_v2/methods/workflow_common.py`，由 E1b_v2 自定 host / survey envelope
