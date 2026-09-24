# AvidBench

**它回答的问题**：Avid 这个 harness 到底有没有把任务做得更能成、以及那个"更能成"是不是
用更多 token、更多轮次换来的。

**它不回答的问题**：代码有没有 bug（那是 `tests/` 的事）、模型本身聪不聪明（那是模型的
benchmark，不是 harness 的）。三层的关系见 `docs/status/BENCHMARK.md`。

## 怎么跑

```bash
uv run --env-file .env python -m benchmarks.run --list                    # 看评测集（含 tier）
uv run --env-file .env python -m benchmarks.run --smoke                   # 3 条 case × 3 变体，先验仪器
uv run --env-file .env python -m benchmarks.run --suite v1                # 只跑 v1 那 9 条难度 case
uv run --env-file .env python -m benchmarks.run --suite v1 --variants core,full \
    --context-chars 30000 --out benchmarks/runs/tight                     # 单变量对照：只改阈值
uv run --env-file .env python -m benchmarks.run                           # 全部 suite（21 条 × 3 变体）
```

也可以走 pytest 薄壳（`tests/test_bench_eval.py`）：

```bash
uv run --env-file .env pytest -q -m eval_smoke -s   # 冒烟
uv run --env-file .env pytest -q -m eval -s         # 全量
```

默认 `pytest` **不跑**评测（`eval` / `eval_smoke` 都不在 addopts 里）。真模型调用有成本
与抖动，它是提交前手动跑的一次实验，不是门禁。评测数字不决定退出码——通过率是测量结果。

**pytest 路径有一个必须知道的坑**：`tests/conftest.py` 的 `model_env` 是 autouse 的，会给
每个测试塞 `AVID_API_KEY=test-key`。它现在对 `eval` / `eval_smoke` 标记让开，并且
`support.real_config_or_skip()` 会在拿到那对假配置时**直接失败**——否则一次 401 会让全部
运行变成 `llm_error` 而测试仍然绿（实测踩到过一次）。

## case 集版本（suite）与难度（tier）

`cases/` 下**一个目录就是一个冻结的版本**：`v0`（12 条基础/恢复/会话）、`v1`（9 条难度）。
`--suite` 是唯一选择器（`v0` / `v1` / `all`）。

规则只有一条，但它是硬的：**一个版本一旦有基线落盘就不再改**——要改 case、fixture 或判定器，
就新建下一个版本。跨版本的差值不可比，因为它们不是同一份任务集（`BENCHMARK.md` §9 与 §10
记着每个版本各自的基线与环境）。

难度用 `tier`（1–5，可空）声明，按**需要多少决策 / 多少失败点 / 依赖多深 / 上下文多长**计，
不按文件数或模块数计。v1 的 9 条分三组，每组对准一个机制的适用场景：

| 组 | case | tier | 为什么这组能显出机制差异 |
|---|---|---|---|
| 长上下文 | `c01` 36 片日志 / `c02` 规则+30 片 / `c03` 40 跳链 | 3–4 | 消息数被推过 snip 阈值（50），早期结论与「读到哪了」都要自己维护 |
| 依赖规划 | `c04` 隐藏 DAG / `c05` 废弃跳转 / `c06` 8 源合并 | 3–5 | 顺序与状态不写在题面里，靠 TODO 清单跟踪才能不漏步 |
| 委派校验 | `c07` 12 个独立模块 / `c08` 两份清单核对 / `c09` 先失败再补缓存 | 3–4 | 子任务可并行；不一致要交叉验证；错误信息里写着下一步 |

v0 的 12 条没有 `tier`（它就是「基础」那一档），也**不会被补标**——那个版本已经冻结。

## 三个变体

| 变体   | 循环             | 工具集                              | 压缩/提醒/nudge/hook |
|--------|------------------|-------------------------------------|----------------------|
| bare   | `bare.py` 朴素循环 | read_file / glob / bash            | 关                   |
| core   | `agent_loop`     | 与 bare **完全相同**                | 开                   |
| full   | `agent_loop`     | 全部（+ todo / subagent / skill） | 开              |

`core − bare` = 循环与上下文机制的增益；`full − core` = 子 agent / 技能与 todo 的增益。
三者共用同一份 `SYSTEM_PROMPT`（只有工具清单不同），每次运行会把规格写进 `result.json`
的 `variant_spec`。

跨会话 case（`m01`）只跑 `full`，条件是 `resume`（第二轮带第一轮的真实会话投影）与
`fresh`（只有新 prompt）——`bare` / `core` 没有会话层，这个对照在它们身上不可表达。

## 一条 case 长什么样

`cases/v0/*.toml`，四段：

```toml
id = "b02_column_sum"          # 必须与文件名一致
category = "basic"             # basic / long_horizon / recovery / subagent / task / session
fixture = "b02_column_sum"     # fixtures/ 下的只读目录
tier = 3                       # 可选：难度声明 1–5（v0 的 12 条没有，也不补）
prompt = """
…
回答格式：sum=<数字>
"""

[limits]
timeout_seconds = 240          # 墙钟硬超时：到点 state.cancel("timeout")

[[graders]]
kind = "answer_contains"
text = ["sum=4176"]
```

`followup` 可选，给了就是跨会话 case，grader 只判第二轮。

**判定器只有五种确定性原语**：`command`（退出码 + stdout 子串）、`file_contains`、
`file_equals`、`json_path_equals`、`answer_contains`（归一化：小写、去空白、去逗号）。
`resolved = 全部 grader 通过`（AND，无部分分、无加权）。**不用 LLM judge**：那只是把
不可复现性从被测对象转移到裁判身上。

本阶段 21 条 case 都是只读任务，交付物是最终回答，所以判定器实际只用到 `answer_contains`
与 `command`（后者用来钉住 fixture 不变量：有人改了 fixture，case 会响亮地失败而不是
悄悄判一个过期的真值）。这些「不变量」判定器在**干净 fixture 上必须先通过**，由
`tests/test_bench_cases.py` 钉住。另外三种原语由 `tests/test_bench_graders.py` 单测钉住，
等可写 case 进来再用。

**只读**的准确含义：不改动 fixture 的受判文件。`.avid/`（会话）
是 runtime 写在**工作区里**的簿记目录，判定与 manifest 一律排除它们；case 跑在
`mkdtemp` 出来的临时副本上，`fixtures/` 永不被写。

## 结果在哪

```
benchmarks/runs/<UTC 时间>-<commit>/
├── summary.txt                 # 聚合报表（变体 × resolved / tokens / rounds / 墙钟 / 失败分类）
├── results.json                # 全部 RunResult
└── <case>/<variant>/
    ├── result.json             # 状态 + 指标 + grader 逐条 + variant 规格
    ├── trajectory.jsonl        # 逐条消息与步骤事实（研究材料）
    └── answer.txt
```

`runs/` 不入库（`.gitignore`）。入库的只有评测集本身，以及**一行基线数字**——
写进 `docs/status/BENCHMARK.md`，含 commit、模型、机器、日期与上限设置。

指标**全部从事件流派生**（`on_event` / `on_message`），不从 `RunState` 读，这样两个循环
用同一把尺子量；两边一致由 `tests/test_bench_runner.py` 钉住。

## 边界

- **内核只开了一个口**：`agent_loop(..., budget=...)` 接收压缩阈值（默认 `None` = 与以前
  逐字一致），由 `--context-chars` 注入。别的差异一律只经既有注入点（`chat` / `tools` /
  `registry` / `state` / `hooks` / `on_message` / `on_event`）。硬超时用既有的取消检查点。
  为什么要开这个口：**单变量对照必须在同一个 commit 上跑**，而"改常量再跑一次"会把代码
  差异混进差值里；注入值会写进每次运行的 `overrides`。
- **`overrides` 与 `variant_spec` 一样重要**：没有它，两组对照的数字放在一起无法解释。
- **不建第二套 runtime**：`runner` 只做物化 → 装配 → 跑循环 → 判最终状态 → 落盘。
- **只有三个臂**：完整消融矩阵（±task / ±subagent / ±compaction）要等统计功效与可切换
  机制都到位（跨会话"记忆"机制还不存在）。
- **不测生产路径**：runner 直连 `agent_loop`，不经过 `svc.RunRegistry`。数字代表内核
  循环，不代表 Web 服务层的运行语义——这一条在结果解读时必须记住。
