# 清华短视频数据核查记录

本文件保留来源、字段和本地核查结果，不代表选择该数据集作为论文研究对象。

## 来源与规模

- 论文：Yu Shang, Chen Gao, Nian Li, and Yong Li. *A Large-scale Dataset with Behavior, Attributes, and Content of Mobile Short-video Platform*. WWW 2025 Companion.
- DOI：https://doi.org/10.1145/3701716.3715296
- 预印本：https://arxiv.org/abs/2502.05922
- 代码与说明：https://github.com/tsinghua-fib-lab/ShortVideo_dataset
- 数据服务器：https://fi.ee.tsinghua.edu.cn/datasets/short-video-dataset/

论文报告的完整数据包含 10,000 名用户、153,561 条视频、1,019,568 次交互、3.2 TB 原始视频和 3,998 小时内容。论文使用 8:1:1 划分测试了 BPR、LightGCN、LayerGCN、VBPR、MMGCN、GRCN、LGMRec 和 BM3，其中 BM3 的 Recall@20 为 0.0364、NDCG@20 为 0.0218。这些是论文完整数据结果，不是本地样本的结果。

## 本地样本核查

截至 2026-09-22，公开服务器可获取的核心样本表 `interaction_sampled.csv` 约 153 MB；本地路径为 `datasets/tsinghua_shortvideo/interaction_sampled.csv`，SHA-256 为 `96cadb70829e853b8d7797bc566928eceeb670f19a8a0b92dbfa58b32372bf87`。

- 原始 CSV 794,053 行；按 `user_id + pid + exposed_time` 去重后为 129,483 次曝光；
- 用户 6,654 名，视频 31,496 条，时间为 2022-09-16 至 2022-09-22；
- 用户字段包括年龄、性别、城市、城市等级、社区类型、手机价格；
- 视频字段包括标题、标签、三级类别、作者及粉丝数、视频时长；
- 反馈字段包括点击、观看时长、点赞、评论、关注、收藏、转发和负反馈。

同一次曝光会因多个 `tag_name` 重复成多行，平均约 6.13 行，分析前须合并标签并按曝光去重。公开样本未直接包含 README 所述的 `effective_view`，可依据论文定义由 `watch_time > 3` 秒计算。

本地曾用结构化字段和标题/标签完成时间留出基线：训练期 2022-09-16 至 20 日有 103,109 条去重曝光，21 至 22 日的测试期有 26,374 条；测试集中约 95.1% 的用户和 80.2% 的视频在训练期出现。该基线的测试 ROC-AUC 为 0.557，排名前 30% 的有效观看率约为 74.5%，测试集总体约为 70.5%。这些数字仅证明链路有可学习信号，不是毕业论文的正式结果。

## 数据边界

- 该样本并非论文报告的全部交互数据；完整视频文件体量较大。
- 自然曝光日志存在曝光选择偏差；未曝光的用户-视频组合没有真实反馈，不能宣称离线排序具有在线投放因果效果。
- 没有广告、品牌、商品订单或销售额标签；结果只能是观看与互动。样本点赞率约 2.02%、关注率约 0.46%、转发率约 0.23%。
- 论文页面为 CC BY 4.0，但数据仓库未附单独的标准许可证；引用论文，避免再分发原始数据，正式使用前核对论文使用要求。
