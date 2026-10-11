# E1统一D/R/T数据准备

本目录实现实验手册步骤11--14的数据层：从E0b已验收的家庭-活动权限母表生成完全同构的D/R/T预测输入、D/R标签、T权限连接表、活动事实、确定性Persona输入、D拟合词表与行为分群。

模型输入不包含row_id、家庭ID、活动ID、split、E2权限、行为分群或标签；无标识payload与行号-row_id索引分别保存。行为分群仅保存在独立分析表，不能作为模型特征。连接信息保存在独立私有表中；D/R标签单独保存；程序不打开、不复制也不生成T标签。T活动交易特征按各自START_DAY前84天滚动构造，但历史兑换特征只使用E0b合格且已结束的D/R标签，绝不使用T段兑换结果；E0b排除的5条历史单位不作标签推断或负例填补。

方案展示不会读取真实数据：

```sh
.venv/bin/python thesis/e1_data/build.py
```

真实执行必须在代码提交并推送、工作区干净后进行：

```sh
.venv/bin/python thesis/e1_data/build.py --execute
.venv/bin/python thesis/e1_data/verify.py thesis/e1_data/runs/e1-data-<timestamp>
.venv/bin/python thesis/e1_data/audit_sample.py thesis/e1_data/runs/e1-data-<timestamp>
```

运行目录包含真实家庭连接和行为特征，整体由Git忽略且私有文件权限为`0600`。Git只跟踪代码、冻结规则和不含家庭ID/T结果的聚合验收报告。
