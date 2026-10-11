# 本地 Persona 烟雾测试

## 这次测试验证什么

这条通路验证：本地模型能否根据结构化 Persona 回答 OpinionQA 问题，输出能否稳定解析，并能否与同年龄组真人加权分布计算距离。

它使用与 MatrAIx Survey 兼容的 `Persona -> 问题 -> 选项 -> 结果` 数据结构，但不是 MatrAIx 官方运行时。论文中必须保留这个边界。

## 运行过程

脚本会依次完成：

1. 读取 OpinionQA W26 的问题、真人回答和调查权重。
2. 按年龄组以固定随机种子抽取真实受访者，只保留年龄、性别、教育、地区、婚姻和收入作为 Persona。
3. 确保 Persona 中不含当前问题的真人答案。
4. 调用本地 Ollama 模型，要求只返回 JSON 选项标签。
5. 保存每次调用的时间、完整提示、原始输出、解析结果和错误。
6. 聚合模型选择，与同年龄组真人加权分布计算 TV 和 JS 距离。

如果 Ollama 尚未运行，脚本会启动临时 `ollama serve`，测试结束后只关闭由脚本启动的进程。

## 先跑 1 x 1

```bash
cd thesis/feasibility
python3 local_persona_smoke.py --preset tiny
```

通过标准：生成 1 条回答，`parse_success_rate` 为 `1.0`，并产生 `summary.json`、`responses.jsonl`、`personas.json` 和 `results.json`。

## 再跑 200 条

```bash
python3 local_persona_smoke.py --preset pilot
```

该配置固定为 2 个年龄组、10 道题、每组 10 个 Persona，共 200 条结构化 Persona 回答。通过标准是解析成功率至少 95%，并记录完整运行时间。

三条件正式对照会产生 600 条回答：

```bash
python3 local_persona_smoke.py --preset pilot --conditions no_persona,age,structured
```

每次运行写入独立时间戳目录：`outputs/local_persona_smoke/<run_id>/`，不会覆盖前一次结果。

## 证据文件

- `config.json`：参数、开始时间和实际实验规模。
- `personas.json`：本次抽取的 Persona。
- `responses.jsonl`：逐次提示、原始输出、答案、耗时和错误。
- `results.json`：每个问题、年龄组和条件的分布距离。
- `summary.json`：完成数、解析率、总耗时和平均 TV 距离。
- `ollama_server.log`：仅当脚本自行启动 Ollama 时产生。
- `FAILED.json`：若模型服务启动失败，记录失败时间、阶段和原因；出现此文件不能算测试完成。

只有 `summary.json` 存在且完成数符合计划，才可以说该次烟雾测试已经完成。
