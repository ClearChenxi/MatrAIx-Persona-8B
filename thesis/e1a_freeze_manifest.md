# E1a 协议冻结记录（Protocol Freeze Manifest）

版本：v1.0 · 日期：2026-10-12 · 状态：**待签字**

本记录对应论文实验手册 步骤 20。一旦签字，所有 T 预测严格按此清单执行；任何修改必须按"冻结表变更流程"逐项记录。

---

## 1. 实验层级与范围

| 项 | 值 |
|---|---|
| 父实验 | E1（未来活动响应预测） |
| 子实验 | **E1a**（预测效度与传统方法竞争力） |
| 不包含 | E1b（Persona 机制）、E2（少标签增量）、E3（Hillstrom）—— 由各自冻结记录管理 |
| 主比较族 | P1 vs N0/N1（层次 1 外部信号）；P1 vs B1/B2/B3（层次 2 竞争力） |
| 主指标 | 每活动 Average Precision 等权宏平均（Y1） |
| 主标签 | Y1：coupon_redempt 同家庭+同活动+同有效券 UPC 且 START_DAY ≤ DAY ≤ END_DAY 至少一次记录 |

---

## 2. 数据与时间划分（已冻结，源自 E0b 验收）

| 项 | 值 |
|---|---|
| 原始数据版本 | e1-data-20261011T042328.304949Z |
| D/R/T 活动 | 25 / 4 / 11（10 个 T 活动评价，活动 24 排除） |
| D/R/T 行数 | 729 / 681 / 1,714 |
| T 适配/评价 | 328 / 1,386（fixed seed 20261009，全局家庭 20/80） |
| 历史窗 | START_DAY − 84 至 START_DAY − 1 |
| 包含条件 | Type B/C，84 天内有合法正购买 |
| 排除 | 活动 24（END_DAY=719，超出数据覆盖 DAY 711） |
| 关联表/字段 | links / features / labels / groups 四表完全一致 56 字段 X/S schema |

---

## 3. 方法身份与超参

### N0（D 历史总体兑换率常数）

- **输入**：无（仅 D 冻结的总体兑换率）
- **公式**：`p = sum(y1 for D) / len(D)`
- **回退**：不适用
- **代码**：`thesis/method_dev/methods/run_baselines.py::run_n0`

### N1（仅用 S 的活动级概率模型）

- **输入**：仅活动事实 S（campaign_type, valid_days, unique_coupon_count, dept/commodity/brand_type_count, 8 个 top commodity weight, other_weight, unknown_taxonomy_flag）
- **不使用**：任何家庭 X、家庭 ID、活动 ID
- **模型**：Logistic(C=1.0)，正负活动样本不足时回退到 per-campaign 兑换率
- **同一活动内所有家庭分数相同**（AP 仅反映"活动层排序"）
- **代码**：`run_baselines.py::run_n1`

### B1（RFM 业务规则排序）

| 优先级字段（高 → 低） | 说明 |
|---|---|
| `prior_redeemed_campaign_count` | 历史已结束活动兑换次数 |
| `prior_redemption_rate` | 历史兑换率（无历史者用 −1 单独标记） |
| `−recency_days` | 最近一次正购买距今越近越好 |
| `basket_count` | 84 天购物篮数越高越好 |
| `sales_total` | 84 天正购买 SALES_VALUE 合计越高越好 |
| 平分打破 | `sha256(household_key + "|" + campaign)` 前 4 字节 |

- **不做概率校准**：仅排序分（rank-based score = (n−rank)/n）
- **代码**：`run_baselines.py::run_b1`

### B2（L2 Logistic 回归）

| 项 | 值 |
|---|---|
| 模型 | `sklearn.linear_model.LogisticRegression` |
| 选定正则强度 | **C=0.1**（D time-holdout AP 最高，平局不强正则化） |
| 候选 | C ∈ {0.01, 0.1, 1.0, 10.0} |
| D 上候选 AP | 0.2874 / **0.2946** / 0.2724 / 0.2511 |
| D 时序划分 | 训练 27/28/29（START 237–281），验证 1–7（START 346–398） |
| 数值预处理 | StandardScaler；类别只 one-hot campaign_type |
| 类别编码 | campaign_type ∈ {TypeB, TypeC} 二元 one-hot |
| 不使用 | 家庭 ID、活动 ID、persona 文本 |
| 训练数据 | 完整 D=729（refit on all D 用于 R/T 评价） |
| 校准 | 暂未做 Platt；主比较用未校准原始概率 |
| 代码 | `run_logistic.py` |

### B3（CatBoost）

| 项 | 值 |
|---|---|
| 模型 | `catboost.CatBoostClassifier` |
| 选定超参 | **depth=5, learning_rate=0.1**（D time-holdout AP 最高，平局选更浅） |
| 候选 | depth ∈ {3, 5} × lr ∈ {0.03, 0.1} |
| D 上候选 AP | d3/lr0.03=0.296, d3/lr0.1=0.265, d5/lr0.03=0.296, **d5/lr0.1=0.296** |
| 最大轮数 | 1000 |
| 早停耐心 | 50（在 time-holdout 训练中使用） |
| 数值特征 | 39 个 X/S 数值字段 + 20 个 top-department 支出占比 |
| 类别特征 | campaign_type, top_commodity_1/2/3 |
| 不使用 | 家庭 ID、活动 ID、persona 文本 |
| 训练数据 | 完整 D=729 |
| 校准 | 暂未做 Platt；主比较用未校准原始概率 |
| 代码 | `run_catboost.py` |

### P1（MatrAIx-compatible Persona Agent）

| 项 | 值 |
|---|---|
| 后端 | 本地 Ollama 0.33.3，`qwen3.5:9b`（digest `6754d230d1c3c11ac0c293aba22b304d39a6eb54c3e9954c574c317bcfa0b61c`） |
| 客户端 | `MatrAIx-compatible domain persona agent`（基于官方开源 Survey Agent，本地 Qwen 后端） |
| Agent 实现 | 官方未修改的 `PersonaJsonSurvey` 1.0.0 + `HostEnvironment`，论文适配器 |
| 已冻结提示（v2） | "Using only the observed household facts and campaign facts, estimate the chance of at least one valid coupon redemption during this campaign. Treat unknown information as unknown and do not invent facts. Return only {\"p_redeem\": number}, where number is finite and between 0 and 1." |
| 提示选择规则 | D 上逐活动宏 AP；平局（≤0.0001）选更短提示 |
| D 上 v2 AP | **0.1485**（v1=0.1056, v3=0.1034） |
| 推理参数 | temperature=0, seed=20261012, num_ctx=8192, num_predict=64, format=json, think=false |
| 历史问卷 ID | 通过运行内临时任务映射绑定，结束后恢复 |
| 不读取 | coupon_redempt、hh_demographic、causal_data、T 标签 |
| 失败回退 | 重试最多 2 次；最终无合法概率 → 历史开发/验证集基率 `87/1410 = 0.0617` |
| R 区分度门槛 | ✅ 合法率 100%，66 个不同分数，最高单值占比 12% |
| 代码 | `thesis/method_dev/methods/run_r_v2_pilot.py`（区分度），`thesis/method_dev/run_d_prompt_compare.py`（选提示） |

---

## 4. 预测输出与封存

### E1a 预测集封存包

每个方法产出一份预测表，每行字段：

| 字段 | 内容 |
|---|---|
| `row_id` | 家庭—活动唯一键 |
| `variant` 或 `method_id` | 方法标识（v2 / N0 / N1 / B1 / B2 / B3） |
| `campaign` | 活动 ID（与 links 一致） |
| `p_redeem` | 预测概率 [0, 1]（B1 为 rank_score） |
| `attempts` | 重试记录（仅 P1） |
| `fallback` | 是否回退到基率（仅 P1） |
| `elapsed_seconds` | 单次推理耗时（仅 P1） |
| `config_sha256` | 方法配置 hash（用于审计） |
| `T_read` | 0（必须为 0；非 0 即拒绝封存） |

### 封存条件（步骤 21）

1. 每个方法的所有 T=1,386 评价家庭记录齐全
2. 预测文件不含任何 T 标签摘要或活动级基率
3. SHA-256 与 manifest 中声明一致
4. 输入与预测一一对应（无缺漏、无重复）

---

## 5. E2 计划（仅供 E1a 封存后参考，本记录不冻结 E2）

| 项 | 值 |
|---|---|
| 主活动 | 14 / 16 / 17 / 19 / 20 / 22 / 23 / 25（8 个） |
| n ∈ {0, 10, 20} | 三预算同时 |
| n=40 敏感性 | 20 / 22 / 23 / 25（4 个） |
| 适配池 | 328（按 seed 20261009） |
| 评价池 | 1,386（与 E1a 共享） |
| 共同外层 | n=0 不适配；n>0 用 E2-1/2/3 一维 Logistic 校准器 |
| 主比较 | E2-2 vs E2-1 八活动等权宏 Brier 差（n=10 与 n=20 分报） |

---

## 6. 解封条件（步骤 25）

- 必需 E1a 预测集已封存（且 SHA-256 与 manifest 一致）
- 协议冻结表已签字
- 时间锁 drand Quicknet 第一轮次已达到
- 数据保管方已登记解封日期与版本

---

## 7. 已签字清单（签字前请确认）

| 序号 | 项目 | 状态 |
|---|---|---|
| 1 | E1a 范围与主指标 | ⏳ 待签 |
| 2 | D/R/T 划分（活动 24 排除） | ⏳ 待签 |
| 3 | N0/N1/B1 身份 | ⏳ 待签 |
| 4 | B2 C=0.1 | ⏳ 待签 |
| 5 | B3 depth=5, lr=0.1 | ⏳ 待签 |
| 6 | P1 v2 prompt + qwen3.5:9b | ⏳ 待签 |
| 7 | 失败回退（87/1410） | ⏳ 待签 |
| 8 | 校准策略（暂未做 Platt） | ⏳ 待签 |
| 9 | E1a 预测封存包结构 | ⏳ 待签 |
| 10 | 时间锁封存条件 | ⏳ 待签 |
| 11 | E2 主活动 + 预算 | ⏳ 待签 |
| 12 | E3 是否执行（待定） | ⏳ 待签 |

---

## 8. 变更流程

签字后任何修改必须：

1. 在本表末尾追加新行："变更日期 | 原内容 | 新内容 | 原因 | 是否接触测试结果"
2. 由方案审核者逐项复核
3. 重新签字（不得"跑完看哪项好再决定"）

---

## 9. 签字栏

```
方案制定者（实验者）：___________________ 日期：__________
方案审核者：          ___________________ 日期：__________
数据保管者：          ___________________ 日期：__________
```

---

## 10. 附：当前代码与运行制品清单

| 文件 | 路径 |
|---|---|
| N0/N1/B1 脚本 | `thesis/method_dev/methods/run_baselines.py` |
| B2 脚本 | `thesis/method_dev/methods/run_logistic.py` |
| B3 脚本 | `thesis/method_dev/methods/run_catboost.py` |
| P1 R-v2 pilot | `thesis/method_dev/methods/run_r_v2_pilot.py` |
| 汇总 runner | `thesis/method_dev/methods/run_e1a_dev.py` |
| P1 prompt 选择 | `thesis/method_dev/run_d_prompt_compare.py` |
| e1_data 冻结输入 | `thesis/e1_data/runs/e1-data-20261011T042328.304949Z/` |
| D 提示比较制品 | `thesis/method_dev/runs/d-prompt-compare-20261012/` |
| R-v2 pilot 结果 | `thesis/method_dev/methods/r_v2_pilot_results.json` |

代码提交：`2116160 thesis: add E1a traditional baselines (N0/N1/B1/B2/B3) on D and R`
运行分支：`thesis-integration-3633d8d`
官方基线：`3633d8dab149a9482a71b024418a49ae828cc941`
