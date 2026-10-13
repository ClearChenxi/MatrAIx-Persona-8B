# archive/method_dev/ISOLATION.md — 也写在归档内部

为防止 `archive/method_dev/` 内部脚本被误认为可运行，本目录顶部已有 `DEPRECATED.md`。本文件补充反向边界：

| 调用方式 | 是否允许 |
|---|---|
| 复制 `run_e1_T_predictions.py` 到 e1a_v2 / e1b_v2 | ❌ 拒绝 |
| 复制 `run_e1a_dev.py` 的 v2 prompt 选择逻辑 | ❌ 拒绝（不是 PersonaJsonSurvey 渲染） |
| 复用 `p1_calibration_summary.json` 的 1386 行 | ❌ 拒绝（用了 T 解封后标签） |
| 复用 `runs/e1-evaluation-20261013/e1a_e1b_summary.json` | ❌ 拒绝 |
| 复用 `r_v2_pilot_results.json` 作为 P1 真 workflow 的区分度证据 | ⚠️ 仅可作为"R-区分度技术门槛检查通过"的不完整旁证；不能替代真 P1 的 R 区分度检查 |

主实验论文里凡是要引用本目录任何制品，必须在引用之前写明："摘自 `archive/method_dev/`，仅为历史审计参照，不作为主结果证据"。
