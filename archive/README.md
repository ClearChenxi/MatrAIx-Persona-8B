# archive/

> 论文级归档区。**不入 git**——已经迁移的真实制品保留在此供审计，但不会被任何工具或论文材料反向引用。

子目录：

- `method_dev/` — 2026-10-13 起冻结的原方法开发目录。详见 [`method_dev/DEPRECATED.md`](method_dev/DEPRECATED.md)。

新增归档请保持：

1. 顶部放 `DEPRECATED.md`，写明日期、被冻结原因、不可反向引用的具体边界。
2. 子目录内 `.gitignore` 自洽（即使仓库根忽略，整体仍要确保不进入任何路径）。
3. 不放任何 thesis/e1a_v2/、e1b_v2/、e2_v2/ 可能误用到的脚本。
