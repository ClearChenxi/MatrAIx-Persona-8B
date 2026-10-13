# e2_v2 与 method_dev 的隔离

本目录继承 [`thesis/e1a_v2/ISOLATION.md`](../e1a_v2/ISOLATION.md) 的全部隔离规则。

**E2 的输入是冻结的 E1a/E1b 预测**——只能在 e1a_v2/、e1b_v2/ 全部 prediction set 完成封存后启动。
**禁止反向引用 archive/method_dev/ 的 run_e2_limited_label.py。**

## 审计

```bash
cd /Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B
grep -rn "method_dev" thesis/e2_v2 \
    --include="*.py" --include="*.md" \
    | grep -v "隔离" | grep -v "ISOLATION" | grep -v "DEPRECATED"
# 期望：无输出
```
