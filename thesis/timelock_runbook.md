# 时间锁真实封存操作手册

**目的**：把 E1a + E1b 预测封存后启动 drand Quicknet 两阶段时间锁，确保 T 标签在封存期满前不可被解出。
**注意**：本手册是 **执行清单**，不是脚本——所有命令你必须亲自运行（涉及密钥与外部删除）。

---

## 0. 前置检查

```bash
# 检查 tle 是否可用
which tle
go version

# 检查 drand 端可达
curl -s https://api.drand.sh/public/1 | head -c 200

# 检查 T 预测封存包就绪
ls -la thesis/method_dev/runs/e1a-e1b-T-20261012/private/

# 检查源文件 0444（只读）状态
ls -la thesis/e0b/runs/e0b-*/  # 检查原始数据是否已只读
```

---

## 1. 安装 tle（如果尚未装）

```bash
mkdir -p /Users/jiangchenxi/.local/share/tle
.venv/bin/python thesis/e0b/install_tle.py /Users/jiangchenxi/.local/share/tle

# 验证
ls -la /Users/jiangchenxi/.local/share/tle/tle
/Users/jiangchenxi/.local/share/tle/tle --help
```

---

## 2. 选择 drand 登记时间

**至少比当前 drand 公开最新轮次晚 7 天**。假设当前是 2026-10-12：

| 阶段 | 目标日期 | UTC 时间 | 备注 |
|---|---|---|---|
| 第一轮次（解封适配 Y1）| 2026-10-19 之后 | 任意 UTC | 启动时间锁时验证 |
| 第二轮次（解封最终评价 Y1）| 第一轮次 +7 天 | 任意 UTC | E2 预测封存后 |

**快速查 drand 最新轮次**：

```bash
curl -s https://api.drand.sh/public/latest | python3 -c "import json, sys, datetime; d=json.load(sys.stdin); print('round=', d['round'], 'at', datetime.datetime.utcfromtimestamp(d['round']*3+1692803367))"
```

注：3 秒/轮 + genesis 1692803367。可参考 https://www.drand.online/ 实时查询。

---

## 3. 准备时间锁密文包

按官方 README，6 个密文对象：

- `T_adaptation_labels`（328 条）—— 第一阶段解锁
- `T_final_evaluation_labels`（1,386 条）—— 第二阶段解锁
- 私有标签摘要 + 兑换源 + 交易源 + 原始压缩包 —— 第二阶段解锁

```bash
TLE=/Users/jiangchenxi/.local/share/tle/tle

# 1) 安装 tle（如果未装）
.venv/bin/python thesis/e0b/install_tle.py /Users/jiangchenxi/.local/share/tle

# 2) 准备时间锁密文包（必须在你解锁之前做）
.venv/bin/python thesis/e0b/prepare_timelock.py \
  thesis/e0b/runs/e0b-20261011T033848.685233Z \
  /Users/jiangchenxi/timelock-package \
  --tle $TLE

# 3) 验证密文包
.venv/bin/python thesis/e0b/verify_timelock.py package \
  thesis/e0b/runs/e0b-20261011T033848.685233Z \
  /Users/jiangchenxi/timelock-package/timelock-seal-manifest.json
```

---

## 4. 在 drand 上登记两个轮次

**先登记，后启动 E2**。一旦登记，round number 就固定。

```bash
TLE=/Users/jiangchenxi/.local/share/tle/tle

# 第一轮次：2026-10-19 00:00:00 UTC
# 第二轮次：2026-10-26 00:00:00 UTC
.venv/bin/python thesis/e0b/register_timelock_schedule.py \
  2026-10-19T00:00:00Z 2026-10-26T00:00:00Z \
  --tle $TLE
```

**注意**：
- 登记会修改 `timelock-policy.json`，必须**先 git commit + push** 后再登记
- 登记只允许执行一次（从空状态到两个轮次都登记）
- 程序会拒绝已部分登记的 schedule（防部分重置导致不一致）

---

## 5. 创建两个不同介质副本

按官方要求，两个副本必须：
- 在**不同挂载设备**（不同 `st_dev`）
- 在**不同物理位置**
- 复制回临时目录并重新计算 SHA-256（验证复制完整）

```bash
# 假设你的外部存储挂载在 /Volumes/usb1
# 把密文包复制过去
cp -r /Users/jiangchenxi/timelock-package /Volumes/usb1/timelock-package-replica-A
# 在另一个磁盘
cp -r /Users/jiangchenxi/timelock-package /Volumes/external-b/timelock-package-replica-B

# 创建副本清单
.venv/bin/python thesis/e0b/create_timelock_replicas.py \
  /Users/jiangchenxi/timelock-package/timelock-seal-manifest.json \
  /Users/jiangchenxi/timelock-package/replica-manifest.json \
  --replica /Volumes/usb1/timelock-package-replica-A usb1 \
  --replica /Volumes/external-b/timelock-package-replica-B external-b
```

**副本验证**：

```bash
.venv/bin/python thesis/e0b/verify_timelock.py replicas \
  /Users/jiangchenxi/timelock-package/replica-manifest.json
```

---

## 6. 删除前明文盘点

**这一步必须在删除任何明文之前做**。盘点记录所有可能含 T 标签信息的副本存在。

```bash
.venv/bin/python thesis/e0b/inventory_plaintext.py \
  thesis/e0b/runs/e0b-20261011T033848.685233Z \
  /Users/jiangchenxi/timelock-package/timelock-seal-manifest.json
```

**输出**：`inventory_<timestamp>.json`，记录所有权威路径、运行目录、解压目录、压缩包目录内所有同哈希副本。

---

## 7. 删除/锁定明文

**5 类明文必须不存在或不可读**：

```bash
# 1. T 标签的明文 csv
find thesis/e0b/ -name "labels_T*" -exec chmod 000 {} \;
find thesis/e0b/ -name "labels_T*" -exec mv {} {}.sealed-off \;

# 2. 私有标签摘要
chmod 000 thesis/e0b/runs/e0b-*/private/label_summary_PRIVATE.json 2>/dev/null
mv thesis/e0b/runs/e0b-*/private/label_summary_PRIVATE.json \
   thesis/e0b/runs/e0b-*/private/label_summary_PRIVATE.json.sealed-off 2>/dev/null

# 3. coupon_redempt.csv（可重建 Y1）
chmod 000 thesis/e0b/datasets/dunnhumby/coupon_redempt.csv
mv thesis/e0b/datasets/dunnhumby/coupon_redempt.csv \
   thesis/e0b/datasets/dunnhumby/coupon_redempt.csv.sealed-off

# 4. transaction_data.csv（可重建 Y2）
chmod 000 thesis/e0b/datasets/dunnhumby/transaction_data.csv
mv ... (同上)

# 5. 原始压缩包
chmod 000 thesis/e0b/datasets/dunnhumby/dunnhumby_The-Complete-Journey.zip
mv ... (同上)
```

**再次盘点验证**：

```bash
.venv/bin/python thesis/e0b/audit.py  # 检查权限位是否正确
```

---

## 8. 启动 E2 适配实验（第一轮次到达后）

第一轮次到达时（10/19）执行：

```bash
TLE=/Users/jiangchenxi/.local/share/tle/tle

# 解封适配 Y1（328 条）
.venv/bin/python thesis/e0b/unlock_timelock.py \
  thesis/e0b/runs/e0b-20261011T033848.685233Z \
  /Users/jiangchenxi/timelock-package/timelock-seal-manifest.json \
  adaptation \
  --tle $TLE

# 解封后的 Y1 会以临时文件出现，立即用 E2 脚本读
.venv/bin/python thesis/method_dev/methods/run_e2_limited_label.py \
  --stage adaptation --output thesis/method_dev/runs/e2-adaptation-20261019/
```

---

## 9. 启动 E1a/E1b 解封评价（第二轮次到达后）

第二轮次到达时（10/26）执行：

```bash
TLE=/Users/jiangchenxi/.local/share/tle/tle

# 解封最终评价 Y1（1,386 条）
.venv/bin/python thesis/e0b/unlock_timelock.py \
  thesis/e0b/runs/e0b-20261011T033848.685233Z \
  /Users/jiangchenxi/timelock-package/timelock-seal-manifest.json \
  final_evaluation \
  --tle $TLE

# 解封后立即跑 E1a/E1b 评价
.venv/bin/python thesis/method_dev/methods/evaluate_e1a_e1b.py \
  --stage final_evaluation \
  --output thesis/method_dev/runs/e1-evaluation-20261026/
```

---

## 10. 论文主结果

第二轮次解封后，E1a + E1b + E2 完整 AP/Brier 数据就出来了，写论文主结果章节。

---

## 应急：放弃时间锁怎么办

如果 7 天等不及，可以选择：

1. **认 7 天等**：论文合规
2. **看 T 标签但**只用于**探索性分析**：论文里只能写"探索性 T 阶段"，不能进主结论
3. **重新设计实验**：减少时间锁要求（比如只用单阶段、drand 等待期 1 天）

**默认**：你选择认 7 天等，所以按本手册走完。

---

## 我的建议路径

| 时段 | 你做 |
|---|---|
| 今晚 | 无需操作（我在跑 E1a/E1b T 预测） |
| 明天（10/13）| 安装 tle（步骤 1）；准备密文包（步骤 3）；先别登记 drand，等 E1a/E1b 预测封存完成 |
| 10/13 晚上 | E1a + E1b 预测封存包汇总 + git commit；登记 drand 两个轮次（步骤 4）|
| 10/14-10/15 | 创建副本（步骤 5）；明文盘点 + 删除（步骤 6/7） |
| 10/19 | 解封适配 Y1 + 跑 E2 |
| 10/26 | 解封最终评价 Y1 + 跑 E1a/E1b 评价 |

**关键不变式**：7 天 drand 等待 + 14 天双轮次 = 论文完整结果。
