# 营销用户模拟研究背景资料（历史草稿）

更新时间：2026-09-22

## 一、调研后的核心判断

本课题的背景不应写成“零售企业有大量数据，所以需要 AI”，也不应写成“Persona agent 能够替代真实消费者”。更准确的研究背景是：

1. 零售优惠券营销已经形成了客户交易、活动触达、优惠券兑换和后续购买等可观测数据链路；
2. 企业真正关心的不只是“谁本来会购买”，而是“谁在接受活动后更可能发生目标行为”；
3. 传统响应模型和 uplift 模型擅长数值预测与排序，但通常以固定字段和历史标签为主，难以直接表达一个具体营销方案面对不同客户群体时的接受、犹豫、拒绝及理由；
4. MatrAIx 的 Persona agent 原生任务是让不同 Persona 在给定方案和任务中作答、交互并产生群体响应分布，不是把 Persona 直接变成一个购买概率模型；
5. 是否具有外部行为有效性，需要在明确的营销任务和真实结果上检验，而不能只看生成的解释。

## 二、背景资料（尚未确定论文场景）

随着会员体系、电子优惠券和数字化营销的发展，零售企业能够持续记录客户的购买时间、商品类别、消费金额、折扣使用、营销活动参与和优惠券兑换等信息。营销决策因此逐渐从面向所有客户的统一促销，转向根据客户历史行为和活动情境进行差异化触达。企业在一项优惠券活动实施前，需要回答的并不只是“哪些客户过去购买较多”，还包括“哪些客户更可能对本次活动作出响应”“优惠券适用的商品类别是否与其既有消费习惯匹配”以及“不同客户群体对同一活动的接受程度是否存在差异”。

传统客户响应建模通常使用总体响应率、RFM（最近一次购买时间、购买频率和消费金额）规则、逻辑回归、树模型或推荐模型，根据客户特征预测访问、兑换或购买结果。这类方法具有计算效率高、评价指标清晰和易于在大样本上部署等优点。进一步的 uplift modeling 或增量响应建模试图区分“无论是否营销都会购买”的客户和“因为营销而增加响应”的客户，为有限营销资源的分配提供依据。相关研究已经形成较成熟的模型、树方法和在线营销扩展。然而，这些方法通常依赖已经发生的处理和结果标签；当活动内容或优惠券方案缺乏历史样本时，模型对新方案的情境化比较能力受到限制。与此同时，单一概率或排序分数难以直接表达不同客户群体为何接受、犹豫或拒绝某个方案。

大语言模型驱动的 Persona agent 提供了另一种面向任务的用户模拟方式。MatrAIx 将 Persona record、语言模型和任务接口组合为 Persona agent，并通过 Survey、AI Chatbot、Web 和 App 四类环境记录结构化回答、自然语言理由、交互轨迹、任务完成状态和验证结果。在其营销相关任务中，Persona agent 可以面对价格变化、购买意愿或产品推荐情境，选择预先定义的回答选项并说明原因；多个 Persona 的结果还可以汇总为不同人群的响应分布。这个机制与传统模型的输出不同：传统模型主要给出对历史标签的概率或排序，Persona agent 则模拟一个具有特定背景和行为历史的用户完成任务。

但 MatrAIx 论文同时明确了 Persona simulation 的验证边界。论文报告了 Persona adherence 和 human-grounded extraction quality，说明模型能够较好地遵循给定 Persona，来源抽取字段也具有一定证据支持；这些结果并不等于模拟行为已经与真实消费者行为一致。论文还指出，结果会随执行 Persona 的模型变化，模拟群体不是现实人口的概率样本，价格敏感性和购买意向等输出不能直接视为真实市场份额或个体购买概率。因此，Persona agent 若要用于营销研究，必须与真实交易或调查结果进行外部校验，而不能仅以回答是否自然作为有效性依据。

作为数据实例，dunnhumby The Complete Journey 记录约 2,500 个家庭两年的交易、营销活动、优惠券、兑换和部分匿名属性。它可支持活动前后行为分析，但缺少消费者主观理由和部分活动中每户所收的具体优惠券，也不能将观察差异直接解释为严格的个体因果效应。数据集事实另见 `DUNNHUMBY_AUDIT.md`；目前没有选定主要研究数据。

## 三、背景中的概念边界

### 1. “营销响应”不等于“因果增量”

若数据提供随机处理和对照组，可以研究增量响应；若活动分配机制不明确，只能谨慎地研究离线响应预测、群体差异和相关性。Dunnhumby 的活动参与记录不应自动解释为随机实验。

### 2. “Persona agent 输出”不等于“购买概率”

Agent 的原生输出应定义为任务回答、理由、置信度（若任务要求）和群体响应分布。若需要概率指标，应由多次模拟的选项比例构成，并明确称为模拟 cohort 的选择比例，而不是经过校准的个体真实购买概率。

### 3. “行为 Persona”不等于“完整消费者心理画像”

购买频率、类别偏好和优惠券使用率是观测或聚合行为；“价格敏感”“重视优惠”等只能作为行为代理变量，不能写成已经被问卷验证的心理事实。

### 4. “传统方法比较”不等于“保证 AI 胜出”

传统模型可能在标签充分时具有更高的数值预测精度。Persona agent 的可检验优势假设应放在方案情境、少样本群体比较、理由输出和补充决策信息，而不是预先承诺所有 AUC、Brier 或 uplift 指标都更高。

## 四、建议引用的依据

1. MatrAIx. *MatrAIx: Simulating the World with 8.3 Billion Persona Agents*. 本地论文：`papers/00_MatrAIx_8_3_Billion_Persona_Agents.pdf`。重点参见引言、Section 3、Section 4、Section 5 和 Appendix M。
2. dunnhumby. *The Complete Journey User Guide* 与官方 Source Files 页面。官方页面：https://www.dunnhumby.com/source-files/ 。
3. *Modeling the Response Pattern to Direct Marketing Campaigns*. Journal of Marketing Research, 1995. DOI: `10.1177/002224379503200207`。
4. Rzepakowski, P. and Jaroszewicz, S. *Decision trees for uplift modeling with single and multiple treatments*. Knowledge and Information Systems, 2012. DOI: `10.1007/s10115-011-0434-0`。
5. *Direct marketing decision support through predictive customer response modeling*. Decision Support Systems, 2012. DOI: `10.1016/j.dss.2012.06.005`。
6. *Temporal Uplift Modeling for Online Marketing*. Proceedings of the 30th ACM SIGKDD Conference, 2024. DOI: `10.1145/3637528.3671560`。
7. *Consumer segmentation with large language models*. Journal of Retailing and Consumer Services, 2024. DOI: `10.1016/j.jretconser.2024.104078`。用于补充大语言模型在消费者分群中的新近研究背景，但不能替代对 Persona agent 外部有效性的验证。
