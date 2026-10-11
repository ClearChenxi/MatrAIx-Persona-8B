# 候选公开数据库结构审计

更新时间：2026-09-22

本文记录各数据集的原始表头，以及能否整理成“用户/人群 + 内容或处理 + 结果”的统一 JSON 记录。适配性描述不构成数据集选用结论。

## 1. 统一理解方式

对本项目，最有用的最小记录结构是：

```json
{
  "user": {},
  "item_or_treatment": {},
  "exposure_or_assignment": {},
  "outcome": {},
  "time": null,
  "source": "dataset_name"
}
```

四个问题分别对应：

| 字段 | 要回答的问题 |
|---|---|
| `user` | 这个人或用户群体是谁？ |
| `item_or_treatment` | 他看到的内容、收到的营销处理或面对的场景是什么？ |
| `exposure_or_assignment` | 他是否被展示/触达，以及如何被分配？ |
| `outcome` | 他最终做了什么？ |

注意：下面的JSON是为了说明建模格式而写的规范化示例，不是原始数据文件格式。原始数据仍然是CSV或多表CSV。

## 2. Hillstrom邮件营销数据

来源：Kevin Hillstrom / MineThatData Email Analytics Challenge；可通过 `scikit-uplift` 的 `fetch_hillstrom` 加载。

### 原始CSV表头

```text
recency,history_segment,history,mens,womens,zip_code,newbie,channel,segment,visit,conversion,spend
```

每一行代表一个客户。`segment` 是处理变量，包含 `Mens E-Mail`、`Womens E-Mail`、`No E-Mail`。

### 字段分组

| 原字段 | 含义 | 角色 |
|---|---|---|
| `recency` | 距离上次购买的月份数 | 用户特征 |
| `history_segment` | 过去一年消费金额分段 | 用户特征 |
| `history` | 过去一年消费金额 | 用户特征 |
| `mens` | 过去一年是否购买男装 | 用户特征 |
| `womens` | 过去一年是否购买女装 | 用户特征 |
| `zip_code` | 城市类型：Urban/Suburban/Rural | 用户特征 |
| `newbie` | 是否为新客户 | 用户特征 |
| `channel` | 过去购买渠道 | 用户特征 |
| `segment` | 男装邮件、女装邮件或不发邮件 | 处理/对照 |
| `visit` | 两周内是否访问网站 | 结果 |
| `conversion` | 两周内是否购买 | 结果 |
| `spend` | 两周内消费金额 | 结果 |

### 规范化JSON示例

```json
{
  "user": {
    "recency_months": 6,
    "history_segment": "$200-$350",
    "history_spend": 329.08,
    "bought_mens_before": true,
    "bought_womens_before": true,
    "zip_type": "Rural",
    "is_new_customer": true,
    "past_channel": "Web"
  },
  "item_or_treatment": {
    "channel": "email",
    "creative_category": "womens_merchandise"
  },
  "exposure_or_assignment": {
    "assigned_treatment": "No E-Mail",
    "randomized_trial": true
  },
  "outcome": {
    "visited": false,
    "converted": false,
    "spend": 0.0
  },
  "time": {
    "outcome_window": "two_weeks"
  },
  "source": "hillstrom"
}
```

### 适合研究什么

适合研究邮件/优惠处理的增量效果（uplift、CATE、Qini、AUUC）。不适合研究邮件正文语义，因为数据没有邮件标题、正文、图片或优惠细节。

## 3. 清华WWW 2025短视频数据

本地文件：`datasets/tsinghua_shortvideo/interaction_sampled.csv`。

### 原始CSV表头

```text
user_id,pid,author_id,category_id,category_level,parent_id,root_id,exposed_time,author_fans_count,watch_time,duration,cvm_like,click,comment,follow,collect,forward,hate,tag_name,title,p_hour,p_date,gender,age,mod_price,fre_city,fre_community_type,fre_city_level
```

### 字段分组

| 字段 | 含义 | 角色 |
|---|---|---|
| `user_id` | 用户ID | 主键 |
| `pid` | 视频ID | 主键 |
| `author_id` | 作者ID | 内容特征 |
| `category_id`, `category_level`, `parent_id`, `root_id` | 内容类别层级 | 内容特征 |
| `exposed_time` | 曝光时间戳 | 曝光/时间 |
| `author_fans_count` | 作者粉丝数 | 内容特征 |
| `watch_time`, `duration` | 观看时长、视频时长 | 结果/内容特征 |
| `cvm_like`, `click`, `comment`, `follow`, `collect`, `forward`, `hate` | 用户互动反馈 | 结果 |
| `tag_name`, `title` | 标签、标题 | 内容语义 |
| `p_hour`, `p_date` | 曝光小时、日期 | 时间 |
| `gender`, `age`, `mod_price`, `fre_city`, `fre_community_type`, `fre_city_level` | 性别、年龄、设备价格、城市和社区属性 | 用户特征 |

注意：同一次曝光可能因为一个视频有多个标签而重复多行。本地审计发现，建模前要按 `user_id + pid + exposed_time` 去重并合并标签。

### 规范化JSON示例

```json
{
  "user": {
    "user_id": 7313,
    "gender": "M",
    "age": 42,
    "device_price": 2699,
    "city": "邯郸",
    "city_level": "三线城市",
    "community_type": "unknown",
    "history_persona": {
      "preferred_categories": ["商业", "科技"],
      "mean_watch_ratio": 0.61,
      "like_rate": 0.03
    }
  },
  "item_or_treatment": {
    "video_id": 36548,
    "title": "年轻人送外卖?还是进工厂?",
    "tags": ["商业"],
    "category_id": 22,
    "duration_seconds": 151.4,
    "author_fans": 98409
  },
  "exposure_or_assignment": {
    "exposed": true,
    "exposed_time": 1663339704,
    "exposure_source": "platform_log"
  },
  "outcome": {
    "effective_view": true,
    "watch_time_seconds": 156,
    "clicked": true,
    "liked": false,
    "commented": false,
    "followed": false,
    "collected": false,
    "forwarded": false,
    "negative_feedback": false
  },
  "source": "tsinghua_shortvideo"
}
```

### 适合研究什么

适合研究内容-用户匹配、用户响应预测、Persona语义判断和冷启动。它没有随机处理分配、购买金额或广告订单，因此不能直接证明营销销售增量。

## 4. KuaiRand短视频数据

来源：KuaiRand官方GitHub、Zenodo数据包。完整KuaiRand-27K约23GB；KuaiRand-Pure约194MB，更适合本科先跑通。

KuaiRand不是一个单表，而是至少包括：

1. `log_random_4_22_to_5_08_pure.csv`：随机干预曝光日志；
2. `log_standard_4_22_to_5_08_pure.csv`：标准推荐日志；
3. `log_standard_4_08_to_4_21_pure.csv`：前两周标准日志；
4. `user_features_pure.csv`：用户特征；
5. `video_features_basic_pure.csv`：视频基础特征；
6. `video_features_statistic_pure.csv`：视频统计特征；
7. 可选补充：`kuairand_video_captions.csv`、`kuairand_video_categories.csv`。

### 随机/标准日志表头

```text
user_id,video_id,date,hourmin,time_ms,is_click,is_like,is_follow,is_comment,is_forward,is_hate,long_view,play_time_ms,duration_ms,profile_stay_time,comment_stay_time,is_profile_enter,is_rand,tab
```

### 用户表头

```text
user_id,user_active_degree,is_lowactive_period,is_live_streamer,is_video_author,follow_user_num,follow_user_num_range,fans_user_num,fans_user_num_range,friend_user_num,friend_user_num_range,register_days,register_days_range,onehot_feat0,onehot_feat1,onehot_feat2,onehot_feat3,onehot_feat4,onehot_feat5,onehot_feat6,onehot_feat7,onehot_feat8,onehot_feat9,onehot_feat10,onehot_feat11,onehot_feat12,onehot_feat13,onehot_feat14,onehot_feat15,onehot_feat16,onehot_feat17
```

### 视频基础表头

```text
video_id,author_id,video_type,upload_dt,upload_type,visible_status,video_duration,server_width,server_height,music_id,music_type,tag
```

### 视频文案补充表头

```text
final_video_id,caption,show_cover_text,duration
```

### 视频类别补充表头

```text
final_video_id,first_level_category_id,first_level_category_name,first_level_category_prob,second_level_category_id,second_level_category_name,second_level_category_prob,third_level_category_id,third_level_category_name,third_level_category_prob,fourth_level_category_id,fourth_level_category_name,fourth_level_category_prob
```

### 规范化JSON示例

```json
{
  "user": {
    "user_id": 25621,
    "active_degree": "full_active",
    "is_video_author": true,
    "follow_user_num": 5,
    "fans_user_num": 312,
    "register_days": 3624,
    "persona_history": {
      "preferred_categories": ["运动", "游戏"],
      "mean_play_ratio": 0.48,
      "like_rate": 0.04
    }
  },
  "item_or_treatment": {
    "video_id": 3784,
    "video_type": "NORMAL",
    "duration_ms": 17200,
    "caption": "#篮球训练 今日投篮练习",
    "cover_text": "提升命中率的三个动作",
    "category": {
      "level_1": "运动",
      "level_2": "球类运动",
      "level_3": "篮球"
    }
  },
  "exposure_or_assignment": {
    "exposed": true,
    "is_random_intervention": true,
    "tab": 1,
    "time_ms": 1650485801301
  },
  "outcome": {
    "valid_play": true,
    "play_time_ms": 151024,
    "clicked": true,
    "liked": false,
    "followed": false,
    "commented": false,
    "forwarded": false,
    "negative_feedback": false
  },
  "source": "kuairand"
}
```

### 适合研究什么

这是目前最适合本项目的结构：有用户、视频内容、历史行为、随机曝光标记、多种反馈，并可把早期历史聚合成Persona，再用后续随机曝光验证预测。

## 5. X5 RetailHero Uplift数据

本地压缩包：`datasets/x5_retailhero/retailhero-uplift.zip`。它是多表数据，不是单个CSV。

### `data/clients.csv`

```text
client_id,first_issue_date,first_redeem_date,age,gender
```

### `data/uplift_train.csv`

```text
client_id,treatment_flg,target
```

### `data/purchases.csv`

```text
client_id,transaction_id,transaction_datetime,regular_points_received,express_points_received,regular_points_spent,express_points_spent,purchase_sum,store_id,product_id,product_quantity,trn_sum_from_iss,trn_sum_from_red
```

### `data/products.csv`

```text
product_id,level_1,level_2,level_3,level_4,segment_id,brand_id,vendor_id,netto,is_own_trademark,is_alcohol
```

### `data/uplift_test.csv`

```text
client_id
```

### 规范化JSON示例

```json
{
  "user": {
    "client_id": "000012768d",
    "age": 45,
    "gender": "U",
    "first_issue_date": "2017-08-05T15:40:48",
    "first_redeem_date": "2018-01-04T19:30:07",
    "purchase_persona": {
      "purchase_count_90d": 12,
      "mean_purchase_sum": 86.4,
      "favorite_categories": ["c3d3a8e8c6"]
    }
  },
  "item_or_treatment": {
    "channel": "sms_promotion",
    "treatment_flg": 1
  },
  "exposure_or_assignment": {
    "assigned": true
  },
  "outcome": {
    "target": 1
  },
  "time": {
    "history_cutoff": "before_campaign"
  },
  "source": "x5_retailhero"
}
```

### 适合研究什么

适合研究零售短信/促销的增量购买，但商品字段被脱敏，不能让Persona智能体理解真实商品语义。它更适合传统Uplift和交易行为Persona，不适合内容理解型智能体。

## 6. Criteo Uplift Prediction数据

这是大规模数字广告增量数据，约1,397万行。原始表头为：

```text
f0,f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,treatment,exposure,conversion,visit
```

字段含义：

| 字段 | 含义 |
|---|---|
| `f0`-`f11` | 匿名用户特征，连续数值 |
| `treatment` | 是否进入广告竞价/处理组 |
| `exposure` | 是否实际赢得竞价并看到广告 |
| `conversion` | 是否转化 |
| `visit` | 是否访问 |

### 规范化JSON示例

```json
{
  "user": {
    "anonymous_features": {
      "f0": 0.18,
      "f1": 3.2,
      "f2": 0.0
    }
  },
  "item_or_treatment": {
    "channel": "display_ad"
  },
  "exposure_or_assignment": {
    "treatment": true,
    "actually_exposed": true
  },
  "outcome": {
    "visited": true,
    "converted": false
  },
  "source": "criteo_uplift"
}
```

### 适合研究什么

适合广告增量建模和Uplift基准，但没有可解释的用户属性、广告文本或视频内容，因此不适合Persona智能体作为核心方法。

## 7. UCI In-Vehicle Coupon Recommendation

来源：[UCI Dataset 603](https://archive.ics.uci.edu/dataset/603/in+vehicle+coupon+recommendation)。这是问卷场景数据，不是随机营销实验。实际CSV表头为：

```text
destination,passanger,weather,temperature,time,coupon,expiration,gender,age,maritalStatus,has_children,education,occupation,income,car,Bar,CoffeeHouse,CarryAway,RestaurantLessThan20,Restaurant20To50,toCoupon_GEQ5min,toCoupon_GEQ15min,toCoupon_GEQ25min,direction_same,direction_opp,Y
```

注意原始字段拼写是 `passanger`，不是常见的 `passenger`。

`Y` 表示被调查者是否接受当前场景下的优惠券。

### 规范化JSON示例

```json
{
  "user": {
    "gender": "Female",
    "age": 21,
    "marital_status": "Unmarried partner",
    "has_children": true,
    "education": "Some college - no degree",
    "occupation": "Unemployed",
    "income": "$37500 - $49999",
    "car": null,
    "monthly_habits": {
      "bar": "never",
      "coffee_house": "never",
      "carry_away": "4~8"
    }
  },
  "item_or_treatment": {
    "coupon_type": "Restaurant(<20)",
    "expiration": "1d"
  },
  "exposure_or_assignment": {
    "scenario": {
      "destination": "No Urgent Place",
      "passenger": "Alone",
      "weather": "Sunny",
      "temperature": 55,
      "time": "2PM"
    }
  },
  "outcome": {
    "accepted_coupon": true
  },
  "source": "uci_in_vehicle_coupon"
}
```

### 适合研究什么

适合做优惠券接受预测、场景化Persona和可解释分类。它没有随机分组，也没有真实购买结果，因此不能直接做营销增量效果。

## 8. KuaiRec完全观测短视频数据

KuaiRec与KuaiRand都来自快手，但用途不同。KuaiRec强调几乎完整的用户-视频反馈矩阵，适合离线推荐评价。

关键表包括：

```text
big_matrix.csv: user_id,video_id,watch_ratio
small_matrix.csv: user_id,video_id,watch_ratio
social_network.csv: user_id,friends
item_categories.csv: item_id,category
item_daily_features.csv: item_id,date,show_cnt,show_user_num,play_cnt,play_user_num,...
user_features.csv: user_id,user_active_degree,is_lowactive_period,is_live_streamer,is_video_author,...
```

它没有KuaiRand的随机曝光标记，也没有天然的广告处理变量；因此更适合推荐模型，不如KuaiRand适合验证Persona的真实行为预测。

## 9. OpinionQA真人问卷数据

这是前期MatrAIx公共议题方向的数据，不是营销数据。

### 题目表

```text
key,question,options
```

### 人口属性元数据表

```text
key,options
```

### 真人回答表

本地文件包含完整问卷字段，表头很宽，包含人口属性、题目回答和调查权重，例如：

```text
QKEY,...,SAFECRIME_W26,...,CREGION,AGE,SEX,F_EDUCCAT_FINAL,...,WEIGHT_W26,WEIGHT_W25W26,RACE
```

### 规范化JSON示例

```json
{
  "user": {
    "age": "65+",
    "sex": "Female",
    "region": "Midwest",
    "education": "Some college, no degree"
  },
  "item_or_treatment": {
    "question_key": "SAFECRIME_W26",
    "question": "How safe is your local community from crime?",
    "options": ["Very safe", "Somewhat safe", "Not too safe", "Not at all safe"]
  },
  "outcome": {
    "answer": "Very safe"
  },
  "source": "opinionqa_w26"
}
```

## 10. 结构性结论

| 数据集 | 用户可解释性 | 内容/处理可解释性 | 真实结果 | 随机/反事实能力 | Persona智能体适配性 |
|---|---:|---:|---:|---:|---:|
| Hillstrom | 中 | 低 | 购买、消费金额 | 强 | 中低 |
| 清华短视频 | 强 | 强 | 观看、互动 | 弱 | 高 |
| KuaiRand | 中 | 中高 | 多种视频反馈 | 较强，含随机曝光 | 高 |
| X5 | 强 | 低到中 | 购买标签 | 中等，分组说明需谨慎 | 中 |
| Criteo | 低 | 低 | 访问、转化 | 强 | 低 |
| UCI优惠券 | 强 | 强 | 接受优惠券 | 无 | 中高，但不是因果实验 |
| KuaiRec | 中 | 中 | 观看比例 | 高覆盖但非随机处理 | 中高 |
| OpinionQA | 强 | 不适用 | 问卷回答 | 有真人标尺但非营销 | 高，但不属于营销响应数据 |

## 11. 通用输入输出示例（非既定实验方案）

一个可供后续研究设计参考的接口示例：

```json
{
  "persona": {
    "demographics": {},
    "behavior_summary": {},
    "preferences": []
  },
  "candidate_content": {
    "title_or_caption": "...",
    "category": "...",
    "duration": 15.2
  },
  "task": "predict_effective_view",
  "output": {
    "probability": 0.0,
    "reason": "...",
    "confidence": 0.0
  }
}
```

该JSON是给Persona智能体的输入/输出协议；真实数据库的反馈标签仍然是最后的评价标尺。
