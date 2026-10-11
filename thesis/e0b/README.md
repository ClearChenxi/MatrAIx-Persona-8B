# E0b 数据、时间、样本与标签封存审计

状态：已确认执行方案，尚未运行真实审计。E0b不调用模型，不评价预测性能，不读取或公开T响应摘要。

本阶段登记Dunnhumby八张原始表，审计Type B/C活动、券/商品连接、84天历史覆盖、活动期结果覆盖、D/R/T划分和活动依赖；随后按全局家庭ID与固定种子20261009预留20% E2适配家庭、80%最终评价家庭。划分不使用标签。

真实运行分为两个程序。`audit.py`只语义读取五张结构/行为表；兑换、人口和causal表在第一步仅登记文件哈希、行数和表头。代码提交推送后才执行。`seal_labels.py`随后单独读取兑换表，在本地Git忽略目录构造Y1和Y2；T逐户标签及结果摘要权限设为仅文件所有者读取。公开文件只发布允许开发的D/R聚合标签摘要，不写T正例数、响应率或逐户结果。Y3在SALES_VALUE净额和退货语义独立确认前不构造。

方案展示不读取原始数据：

```sh
.venv/bin/python thesis/e0b/audit.py
```

真实执行须从已推送、干净的实验分支启动：

```sh
.venv/bin/python thesis/e0b/audit.py --execute
.venv/bin/python thesis/e0b/seal_labels.py thesis/e0b/runs/e0b-<timestamp>
.venv/bin/python thesis/e0b/verify.py thesis/e0b/runs/e0b-<timestamp>
```

原始数据、家庭ID、权限清单、标签和逐条异常不上传GitHub。代码、方案、聚合结构审计及不含T结果的验收报告提交实验分支。原始文件保持只读；任何源文件在运行中发生变化都会使审计失败。
