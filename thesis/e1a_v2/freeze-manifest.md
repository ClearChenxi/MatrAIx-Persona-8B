# E1a 协议冻结记录（Protocol Freeze Manifest）

**版本**：v2.0 · 日期：2026-10-14 · 状态：**待签字**

> **历史说明**：本 v2 替代 2026-10-12 在 `thesis/method_dev/` 下的 v1 草稿。v1 的致命问题（裸 `urllib.request` + 手写 prompt 伪装 PersonaJsonSurvey + 用解封后 T 标签做 OOF 校准 + 30 min timelock 违规）详见 `archive/method_dev/DEPRECATED.md` 和 `thesis/e0b/timelock-20261013.md`。本 v2 严格遵循 `thesis/experiment-blueprint/` 三件套（README.md、实验执行手册.md、REVIEW_FIXES.md）+ `thesis/e0a/方法身份记录.md` 的方法身份验收结论。

---

## 1. 实验层级与范围

| 项 | 值 |
|---|---|
| 父实验 | E1（未来活动响应预测） |
| 子实验 | **E1a**（预测效度与传统方法竞争力） |
| 同级子实验 | E1b（Persona 机制；独立 freeze-manifest _mm_v2/freeze-manifest.md） |
| 不包含 | E2（少标签增量；e2_v2/）、E3（Hillstrom） |
| 主比较族 | (a) P1 vs N0/N1（层次 1 外部信号，Bonferroni 97.5%）；(b) P1 vs B1/B2/B3（层次 2 竞争力，Bonferroni 98.3333%） |
| 主指标 | 每活动 Average Precision 等权宏平均（Y1） |
| 主标签 | Y1：coupon_redempt 同家庭+同活动+同有效券 UPC 且 START_DAY ≤ DAY ≤ END_DAY 至少一次记录 |
| 主 AP | Average Precision 标准定义，不换 ROC-AUC 或 PR 曲线面积 |

---

## 2. 数据与时间划分（已冻结，源自 E0b 验收）

| 项 | 值 |
|---|---|
| 原始数据版本 | `thesis/e1_data/runs/e1-data-20261011T042328.304949Z` |
| D/R/T 活动 | 10 / 4 / 10（活动 24 因 END_DAY=719 超出数据覆盖固定排除） |
| D/R/T 行数 | 729 / 681 / 1,714 |
| T 适配/评价 | 328 / 1,386（固定 seed `20261009`，全局家庭 20%/80% 划分） |
| 历史窗 | `START_DAY − 84 ≤ DAY < START_DAY`（当天不进入画像） |
| 包含条件 | Type B/C 活动，且 84 天内有合法正购买 |
| 主标签 Y1 | 唯一主标签；Y2（目标商品购买）、Y3（目标商品净额）作辅助结果 |
| 不读取 | coupon_redempt.csv（已 chmod 444）、transaction_data.csv（已 chmod 444）、original_archive.zip（已 chmod 444）、labels_T_SEALED.csv（已 chmod 000 + rename `.sealed-off`） |

---

## 3. 方法身份与超参

### N0（D 历史总体兑换率常数）

| 项 | 值 |
|---|---|
| 输入 | 无 |
| 公式 | `p = sum(y1_redeemed in D) / len(D)` |
| 验证（D 上实测）| `0.0549`（=40/729） |
| 代码 | `thesis/e1a_v2/methods/baselines.py::run_n0` |
| 不读取 T 标签；不解封任何封存制品 |

### N1（仅用 S 的活动级概率模型）

| 项 | 值 |
|---|---|
| 输入 | 仅活动事实 S（campaign_type, valid_days, coupon/product counts, dept/commodity/brand_type counts, top-8 commodity weights） |
| 不使用 | 任何家庭 X、家庭 ID、活动 ID |
| 模型 | Logistic(C=1.0)，正负活动样本不足时回退到 per-campaign 兑换率 |
| 同一活动内分数相同 | AP 仅反映"活动层排序"，不宣称识别高响应家庭 |
| 代码 | `thesis/e1a_v2/methods/baselines.py::run_n1` |

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
- **不计算 Brier / log loss**：B1 是排序，不是概率
- 代码 | `thesis/e1a_v2/methods/baselines.py::run_b1`

### B2（L2 Logistic 回归）

| 项 | 值 |
|---|---|
| 模型 | `sklearn.linear_model.LogisticRegression` |
| 选定正则强度 | **C=0.1**（D time-holdout AP 最高，平局不强正则化） |
| 候选 | C ∈ {0.01, 0.1, 1.0, 10.0} |
| D 上 macro_AP | 1.0（拟合完美——D=729 单数据集上预期）|
| R 上 macro_AP | 0.260（与 B1 0.263 相近） |
| R 上 Brier | 0.0594 |
| 数值预处理 | StandardScaler；类别只 one-hot campaign_type |
| 类别编码 | campaign_type ∈ {TypeB, TypeC} 二元 one-hot |
| 不使用 | 家庭 ID、活动 ID、persona 文本 |
| 训练数据 | 完整 D=729 |
| 校准 | Platt 校准候选在 R 上比较，主比较用未校准 |
| 代码 | `thesis/e1a_v2/methods/baseline_logistic.py` |

### B3（CatBoost）

| 项 | 值 |
|---|---|
| 模型 | `catboost.CatBoostClassifier` |
| 选定超参 | **depth=5, learning_rate=0.1**（D time-holdout AP 最高，平局选更浅） |
| 候选 | depth ∈ {3, 5} × lr ∈ {0.03, 0.1} |
| D 上 macro_AP | 1.0（拟合完美）|
| R 上 macro_AP | 0.221 |
| R 上 Brier | 0.0649 |
| 最大轮数 | 1000 |
| 早停耐心 | 50（在 time-holdout 训练中使用） |
| 数值特征 | 39 个 X/S 数值字段 + 20 个 top-department 支出占比 |
| 类别特征 | campaign_type, top_commodity_1/2/3 |
| 不使用 | 家庭 ID、活动 ID、persona 文本 |
| 训练数据 | 完整 D=729 |
| 代码 | `thesis/e1a_v2/methods/baseline_catboost.py` |

### P1（MatrAIx-compatible Persona Agent）

| 项 | 值 |
|---|---|
| 后端 | 本地 Ollama 0.33.3，`qwen3.5:9b`（digest `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`）|
| 客户端 | **MatrAIx-compatible domain persona agent**（基于官方开源 Survey Agent，本地 Qwen 后端）—— 非官方托管 MatrAIx；论文不可声称"MatrAIx 产品优于传统方法" |
| Agent 实现 | 官方未修改的 `matraix.agents.persona.json_survey:PersonaJsonSurvey` 1.0.0 + `harbor.environments.host:HostEnvironment`，无 CLI job scheduler |
| 任务模板 | `application/tasks/survey_thesis-e0a-synthetic`（e0a-validated；复用于 E1a，instrument_id `thesis_e0a_synthetic_v1`） |
| 推理参数 | temperature=0, seed=20261012, num_ctx=8192, **num_predict=256**（e0a 默认 64 在 schema-full 输出下被截断，已提升并审计） |
| Ollama options | `{"num_ctx": 8192, "num_predict": 256}` |
| protocol_alias | `openai/qwen3.5:9b`（litellm 协议前缀；Adapter 同时接受裸 `qwen3.5:9b` 与 `openai/qwen3.5:9b`） |
| Adapter | `thesis/e1a_v2/methods/persona_workflow.py::_AdapterHTTPServer`（端口 0，由 `os.environ["OPENAI_BASE_URL"]` 暴露） |
| Adapter 校验 | 仅校验 `(1) path == "/v1/chat/completions"` `(2) model ∈ {MODEL, openai/MODEL}` `(3) response_format == {"type": "json_object"}` `(4) messages == 准备值` |
| 不读取 | coupon_redempt、hh_demographic、causal_data、T 标签、T 标签明文 |
| 失败回退 | 最多重试 2 次；最终无合法概率 → D 冻结总体兑换率（per 手册 §19：不允许用 T 基率） |
| R 区分度门槛（dry-run）| ✅ 100% 合法率（≥0.99），多个不同合法分数（≥3） |
| 代码 | `thesis/e1a_v2/methods/persona_workflow.py::run_method` |

---

## 4. P1 与 E1b LLM 方法的架构边界（**关键变更**）

按手册 §17 + REVIEW_FIXES §8.3，E1a/E1b 的 6 个 LLM 条件走两条不同的代码路径：

| 条件 | 路径 | 关键约束 |
|---|---|---|
| L0/L1/L2 | 直接 LLM（OpenAI SDK → Adapter → Ollama） | **不**走 PersonaJsonSurvey；手工构造 (system, user) |
| P1/P2/P3 | PersonaJsonSurvey + HostEnvironment | 走 Survey Agent；通过 Adapter 调 Ollama |

详细实现见 `thesis/e1a_v2/methods/persona_workflow.py::PERSONA_AGENT_METHODS / DIRECT_LLM_METHODS`。

**L2 vs P1 消息分离**（dry-run 验证）：
- L2: `_build_narrative_block(behavior)` 手工构造，字段顺序按 `BEHAVIOR_KEYS_FULL`，Title Case 显示
- P1: 走 e0a `PERSONA_SYSTEM_TEMPLATE`，Title Case with `-` 拼接
- 字段内容、字段顺序、数值精度**三者均相同**（§17 满足）
- 但**完整 messages sha256 不同**（§8.3 不需要合并）

详细 L0/L1/L2/P1/P2/P3 消息 sha256 记录在 `thesis/e1a_v2/runs/_all6_*_d1/<row_id>_<cond>/expected_messages.json`。

---

## 5. 预测输出与封存

每个方法产出一份 jsonl，每行字段：

| 字段 | 内容 |
|---|---|
| `row_id` | 家庭—活动唯一键 |
| `campaign` | 活动 ID |
| `method_id` | 方法标识 |
| `p_redeem` | 预测概率 [0, 1]（B1 为 rank_score） |
| `fallback` | 是否回退到 D 冻结基率 |
| `elapsed_seconds` | 单次推理耗时 |
| `config_sha256` | 方法配置 hash（用于审计） |
| `message_sha256` | 渲染消息 hash（LLM 方法） |
| `status` | `passed` / `failed` |
| `error` | 失败 trace（status=failed 时） |

封存条件（手册 §21）：
1. 每个方法的 T 评价家庭（1,386 行）+ T 适配家庭（328 行）记录齐全
3. 预测文件不含任何 T 标签摘要、活动级基率或耦合变量
5. 输入与预测一一对应（无缺漏、无重复）
7. `fallback=True` 的记录数与 `raw_response.json` 失败 trace 一致

---

## 6. 时间锁与解封

| 项 | 值 |
|---|---|
| drand chain | Quicknet（chain_hash `52db9ba7...e971`） |
| 阶段 1（adaptation）解封 Y | 登记于 `thesis/e0b/timelock-policy.json`，间隔 1h |
| 阶段 2（final_evaluation）解封 Y | 登记于同文件，第二轮在第一轮后 1h |
| 总窗口 | 2h（短于手册默认 7 天；论文 disclosure 必须写明依据） |
| 短窗依据 | 本地 Quicknet 1200 round 深度 + BLS threshold + 完整两阶段机制足以击败机会主义提前解封 |
| T 标签明文删除 | `labels_T_SEALED.csv` × 2 e0b runs 均已 `chmod 000` + rename `.sealed-off` |
| 解封条件 | E1a + E1b 预测全部封存 + E2 预测封存 + 异常处理全部决定 |

---

## 7. E1b 计划（独立 freeze-manifest）

详见 `thesis/e1b_v2/freeze-manifest.md`。

E1b 五个机制对比（手册 §17）：

| 对比族 | 唯一解释 |
|---|---|
| L1 − L0 | 客户事实整体价值 |
| L2 − L1 | Persona 表达增益 |
| P1 − L2 | Agent workflow 增益 |
| P1 − P2 | 正确客户—画像关联价值 |
| P1 − P3 | 丰富行为画像价值

P4（第二基础模型复核）作为方向复核，不与主模型合并。

---

## 8. E2 计划（e2_v2/，仅供 E1a/E1b 封存后参考）

| 项 | 值 |
|---|---|
| 主活动 | 14 / 16 / 17 / 19 / 20 / 22 / 23 / 25（8 个）|
| n ∈ {0, 10, 20} | 三预算同时 |
| n=40 敏感性 | 20 / 22 / 23 / 25（4 个）|
| 适配池 | 328（按 seed 20261009） |
| 评价池 | 1,386（与 E1a 共享） |
| 共同外层 | n=0 不适配；n>0 用 E2-1/2/3 一维 Logistic 校准器 |
| 主比较 | E2-2 vs E2-1 八活动等权宏 Brier 差（n=10 与 n=20 分报） |

---

## 9. 已签字清单（签字前请确认）

| 序号 | 项目 | 状态 |
|---|---|---|
| 1 | E1a 范围与主指标 | ⏳ 待签 |
| 2 | D/R/T 划分（活动 24 排除） | ⏳ 待签 |
| 3 | N0/N1/B1/B2/B3 身份与 D 训练集 R 验证指标 | ⏳ 待签 |
| 4 | P1 PersonaJsonSurvey + Qwen 路径 | ⏳ 待签 |
| 5 | E1b L0/L1/L2 直接 LLM vs P1/P2/P3 PersonaJsonSurvey 路径分离 | ⏳ 待签 |
| 6 | L2 vs P1 消息分离（§8.3 不需要合并）| ⏳ 待签 |
| 7 | 失败回退 = D 冻结基率（per §19）| ⏳ 待签 |
| 8 | 1h 双阶段 timelock + 短窗 disclosure | ⏳ 待签 |
| 9 | T 标签明文 chmod 000 + rename .sealed-off | ⏳ 待签 |
| 10 | E1a 预测封存包结构 | ⏳ 待签 |

---

## 10. 变更流程

签字后任何修改必须：
1. 在本表末尾追加新行："变更日期 | 原内容 | 新内容 | 原因 | 是否接触测试结果"
2. 由方案审核者逐项复核
3. 重新签字（不得"跑完看哪项好再决定"）

---

## 11. 签字栏

方案制定者（实验者）：___________________ 日期：__________
方案审核者：          ___________________ 日期：__________
数据保管者：          ___________________ 日期：__________