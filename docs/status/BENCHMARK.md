# Avid 性能与效果基准

本文件记录**性能与效果的基准**：有什么数字、缺什么数字、首个基线该怎么建。

**当前结论（一句话）**：**仪器、两套 suite 的基线（§9 / §10）与一次真实的单变量对照都跑过了。
对照的结论是「量到抖动、没量到压缩」**——45 次运行里最大的 transcript 只有 **4,657 字符**，
而阈值是 400,000，**差 86 倍**，三档阈值下 `compactions` 全是 0。加难度也没换来区分度：
9 条 tier 3–5 的只读 case 上三个变体仍然 **9/9**，因为 94 次工具调用里 **72 次是 `bash`**——
只读任务的难度天花板不是"步数"，而是"能不能一条 shell 折叠掉"。

**更新时机**：基准首次建立、阈值冻结、或跑出一轮新基线时。每次记录必须写清测量环境与命令。

**写作方式说明**：初稿（commit `8ed1801`）没有运行任何测量，所以逐条标了出处并区分「仓库内
可复核 / 本地记录不可复核」；**本版跑了一轮全量评测**（36 次真模型运行，commit `1ece312`），
命令、机器与结果在 §9，§3.3 与 §4 的规模数字也在本轮重测过。两轮写作方式的差别本身就是这份
文件存在的理由：可以复核的数字必须是命令跑出来的，不是回忆出来的。

---

## 1. 结论

| 问题 | 答案 |
|---|---|
| 有任务成功率 / 通过率吗？ | **有**：两套 suite 各自的基线——v0（12 条，§9）与 v1（9 条难度 case，§10.2） |
| 有性能基线（当前值 + 可重复的测量命令）吗？ | **效果类有**（§9 / §10 的命令与数字）；**性能类仍然没有**（耗时口径未定、机器未固定） |
| 有「改动前后对比」机制吗？ | **有，而且本轮真跑过一次**：同 commit、同 suite，只差 `--context-chars`（§10.3）。结论是机制没被触达，差值只是抖动——这条对照因此还给出了抖动尺子（full ±10% / core ±4%） |
| 有防退化的复杂度门禁吗？ | **有**：3 条 stress 用例（§3.1） |
| 有体积/资源上限吗？ | **有且已冻结**：`web/budget.json` 的 `frozen_at` 已填，首屏 JS gzip 实测 184,160 B / 460,800 B（§3.2） |
| 有测试规模的数字吗？ | 有静态计数（§3.3）与**本轮实测的收集数**（1015 收集 / 1010 passed / 5 deselected）；但测试项数是**实现规模**，不是能力或效果指标 |

一句话概括现状：**仓库现在既有仪器也有尺子**——两套 suite 的基线、可复核的抖动幅度、以及
「跨 suite 不可比」的版本规则；它**还回答不了**"哪个机制更有用"，因为唯一被证明能拉开差距的
维度（跨会话投影）只有一条 case，而只读任务族被 `bash` 折叠掉了。

## 2. 「不存在」的清单与证明

| 要找的东西 | 命令 | 实际输出（本版复核） |
|---|---|---|
| 评测 / 基准目录 | `ls -d eval evals benchmarks bench` | **`benchmarks`** 存在（commit `569992e` 起）；其余四个仍 `No such file or directory` |
| 通过率 / 准确率 / 任务集 | `grep -rniE '通过率\|pass rate\|pass@\|准确率\|accuracy\|任务集' src/avid --include='*.py'` | 内核零命中；评测集在 `benchmarks/cases/v0/`（12 条）与 `benchmarks/avidbench/`（runner），不在 wheel 里 |
| 成本 / token 台账 | `grep -rn 'usage\|total_tokens' src/avid --include='*.py'` | 仍只有运行内计数；**但 AvidBench 现在把每次运行的 tokens 落盘**（`benchmarks/runs/*/*/result.json`），首个基线见 §9 |
| 前端性能测量脚本 | `ls web/scripts/`；`grep -rln 'performance\.now\|console\.time' web/src web/scripts web/e2e` | 只有 5 个门禁/构建脚本（`check-layers` / `check-style` / `check-tokens` / `copy-dist` / `gate-size`），无 `perf:*`；计时 API 零命中 |
| 前端 a11y / 视觉回归用例 | `grep -rniE 'axe\|accessibility\|toHaveScreenshot\|snapshot' web/e2e` | 零命中——`web/playwright.config.ts` 写了截图约定，但**没有一条截图断言** |
| 覆盖率 / pre-commit / 任务入口 | `ls Makefile justfile .pre-commit-config.yaml` | 三者都不存在；`pyproject.toml` 也无 coverage 配置 |

「不存在」还间接体现在：`dev/plan/roadmap.md` 自认「已明确的必经项（尚未成为阶段）：加入评测集」，
`docs/design/frontend-architecture.md:988` 的未验证假设清单第 1 条写着「全部性能目标值——
未做任何实测；阈值待第一次测量前冻结」（两处均为本地记录，仅作线索）。

## 3. 仓库内确实存在的数字

### 3.1 stress 门禁：3 条，先断言机制、时间只作第二道保险

`tests/test_stress.py`（165 行），`pytestmark = pytest.mark.stress` 是全仓唯一一处 `pytestmark`。
默认 `addopts = "-m 'not stress' --strict-markers"`（`pyproject.toml:65-71`），CI 里有独立的
stress job 跑 `pytest -q -m stress`（`.github/workflows/ci.yml:25-37`）。

| 用例 | 规模 | 断言（逐字） | 类型 |
|---|---|---|---|
| `test_stress_session_list_does_not_replay_each_session` | 200 个会话 × 60 条（正文 2000 字符） | `opened == []`（**一次都不 open 会话文件**）；`elapsed < 1.5`；`message_count == 60`；`name == "会话 stress-007"` | 机制 + 时间 |
| `test_stress_delta_emit_stays_linear` | 8000 个 delta，`buffer_size=512` | `elapsed < 0.5`；`absolute_index() == dropped + len(events)` | 时间 + 不变量 |
| `test_stress_long_session_summarize_and_replay_stay_usable` | 单会话 5000 条 | 摘要 `summarize_elapsed < 1.0`；重放 `open_elapsed < 3.0`；`message_count == 5000` | 时间 |

文件头逐字写了阈值口径（`:9-11`）：*「因此这里的阈值都留了 10–30 倍余量：它们该抓的是**复杂度**
（平方级、每会话全量重放），不是几个百分点的抖动。写用例时优先断言机制（"一次都没 open"），
时间只是第二道保险。」* 它是**防退化下限**，不是性能指标——别把它当基准读。

### 3.2 前端体积预算：`web/budget.json`（**已冻结**，2026-09-19）

| 键 | 值 | 含义 |
|---|---|---|
| `entry_gzip_bytes` | 460800 | 首屏入口 JS 逐块 gzip 上限 |
| `chunk_gzip_bytes` | 358400 | 单个异步块上限 |
| `exempt_gzip_bytes` | 204800 | 豁免上限 |
| `font_bytes` | 61440 | 字体体积上限 |
| `texture_bytes` | 32768 | 位图纹理上限 |
| `frozen_at` | `2026-09-19T10:04Z · commit de3035c · WSL2 / node v24.15.0` | **已冻结**——测量环境写在该字段里 |
| `EXEMPT` | `[]` | 空 |

**首次实测（本轮）**：`pnpm -C web build && pnpm -C web run gate:size`。测量后工作区保持干净
（`dist/` 与静态产物都在 `.gitignore` 里）。

| 项 | 实测 | 上限 |
|---|---|---|
| 首屏 JS gzip 合计（4 块） | **184,160 B**（raw 579,261 B） | 460,800 B |
| 单块最大（`vendor`） | 63,912 B gzip（raw 195,814 B） | 358,400 B |
| 字体 0 个文件 | 0 B | 0 B |
| 样式表（.css） | 5,630 B gzip | 16,384 B |
| 位图纹理 0 个 | 0 B | 32,768 B |
| 外部字体请求 | 未命中 | — |

逐块 gzip：`index` 57,580 / `query` 15,170 / `vendor` 63,912 / `markdown` 47,498。

**为什么现在可以冻结**：实测 184,160 B 与本地过程文档里最后一次记录的 184,311 B 相差 0.08%
（§5）——两套独立来源互相印证，不再有「五个版本、阈值还变过一次」的歧义（§4）。改上限必须
同时改 `frozen_at` 的说明并写「为什么改」（§8）。

### 3.3 测试规模（**静态计数**，不是 pytest 收集数）

| 指标 | 值 | 怎么数的 |
|---|---|---|
| 内核测试函数 | **725** 个，分布在 **44** 个 `tests/test_*.py`（另有 3 个支撑文件，`tests/` 共 47 个 `.py`） | `grep -h '^def test_' tests/*.py \| wc -l` |
| 内核测试收集数（**本轮实测**） | **1015 收集**，其中 **1010 passed / 5 deselected**（commit `7c70cc4`，本机 WSL2 / Python 3.12.3；5 = 3 条 stress + 2 条 eval 薄壳） | `uv run pytest -q` |
| `parametrize` | **32** 处 | `grep -c parametrize` 逐个文件求和 |
| 内核测试代码量 | 13,560 行（`tests/*.py`） | `wc -l tests/*.py` |
| 评测仪器规模 | 16 个 Python 文件 / 1,738 行，外带 **21 条 case**（v0 12 + v1 9）与 **225 个 fixture 文件**（`benchmarks/`，**不进 wheel**） | `find benchmarks -name '*.py' \| wc -l`、`wc -l` |
| 前端 vitest | **92** 条，12 个文件（全在 `__tests__/` 下） | `pnpm -C web run test` 实测；静态计数同为 92 |
| 浏览器 e2e | **37** 条 `test(`，9 个 spec | 逐文件计数（静态，本版未重测） |
| `skip` / `xfail` / `skipif` | **0** | `grep` 零命中——没有靠跳过兜绿的用例；评测的 `eval` marker 是「默认不跑」，不是 skip |

**口径警告**：674 是**函数数**；pytest 的收集数会被 `parametrize` 展开，所以上表把两者分开列，
并给出**本轮实测的收集数**（1015 收集 / 1010 passed / 5 deselected）。测试项数增长在任何情况下都
**不能**当作能力或性能指标——它只说明实现规模。

### 3.4 门禁与 CI：抓什么、抓不到什么

| 门禁 | 跑在哪 | 抓什么 |
|---|---|---|
| `ruff check src tests` | CI kernel job + 本地 | 风格与明显错误（规则集显式钉住，不随默认值漂） |
| `mypy` | CI kernel job | `src/avid` 的类型错误 |
| `pytest -q` | CI kernel job | 627 个测试函数展开后的全部普通用例（不含 stress） |
| `pytest -q -m stress` | CI **独立 job** | §3.1 的 3 条复杂度门禁 |
| `pnpm run verify` | CI web job | `check:layers` + `check:tokens` + `lint` + `typecheck` + `test` + `gate:size` |
| `pnpm exec tsc -b` | CI web job | 类型（`verify` 已含 `typecheck`，所以 CI 多跑一次——注释未同步，见 `ARCHITECTURE.md` §8） |

**抓不到的**：e2e（`test:e2e` **不进 CI**——它需要真实模型与起着的内核）、效果类指标（没有）、
a11y 与视觉回归（没有用例）、性能回归（只有 stress 的复杂度下限与体积上限）。

## 4. 数字口径冲突：必须消歧的三处

这三处冲突**就是「没有基准」的直接后果**——同一指标在不同时间点的文件里各写各的，
没有单一权威来源。本文件不裁决谁对，只标出必须重测的项：

| 指标 | 各文件写的值 | 出处（均为本地记录，除注明） | 本文件处置 |
|---|---|---|---|
| 内核测试规模 | `822 passed + 3 deselected`；`869`；`863`；`844`；`835`；`832`；`711` | `dev/review/fix-progress.md:103`；`dev/plan/roadmap.md` 各阶段条目 | **本轮已重测并只认一个口径**：1015 收集 / 1010 passed / 5 deselected（commit `7c70cc4`，本机）。历史值不再引用；以后只认「pytest 收集数 + 环境」这一种写法 |
| 内核测试耗时 | `711 tests in 8.99s` | `dev/review/architecture-review.md:7`、`dev/review/tests.md:14` | 仅历史值；本机与 CI 环境不同，不可比 |
| 浏览器 e2e | `40 项全通过`、`38`、`36`、`35`；静态 **37** | `dev/plan/roadmap.md:192,179,169,150`；静态计数见 §3.3 | 静态与记录不一致（差额未解释）。以**重测**为准 |
| 首屏 JS gzip | 5 个值（见 §3.2） | `dev/review/*`、`dev/plan/roadmap.md` | **本轮消歧**：实测 184,160 B，与本地记录的最后值 184,311 B 差 0.08%；阈值同轮冻结（§3.2） |

## 5. 历次散落实测（本地过程文档记录，**仓库内不可复核**）

下列数字有记录、有机制解释，但**只存在于 `dev/`（不入库、不在任何远端）**，`dev/` 一旦
清掉就永久丢失，且本文件无法复核它们。列在这里的作用是：① 证明「有人量过」；② 作为首个
基线要**重新推导**的清单——别直接把它们抄成基线。

| 项 | 数字 | 出处 |
|---|---|---|
| 会话列表（20 会话 / 5.9 MB） | 冷 54 → 28 ms；热 53 → **0.8 ms**（mtime+size 缓存） | `dev/review/fix-progress.md`（P1-8） |
| 8000 个 delta 的记账 | 1052 → **13.9 ms**（131.5 → 1.74 µs/条） | 同上（P1-9） |
| `meta()` 扫技能目录 | 5.1 ms/次 | `dev/review/architecture-review.md` |
| `Transcript.estimate_chars` | 600 条 1.12 ms/次；增量化后约省 3.3 ms/轮 | `dev/review/architecture-review.md`、`fix-progress.md`（P2-4） |
| fsync / fdatasync | 1.26 ms / 1.27 ms 每次提交 | `dev/review/architecture-review.md`、`fix-progress.md`（P2-3） |
| `bash -lc` 每次调用 | 0.41 s | `dev/review/fix-progress.md`（P2-2） |
| 首屏 JS gzip | 184,311 B / 460,800 | `dev/review/fix-progress.md`（P2-8）——**本轮实测 184,160 B，两套来源差 0.08%，已冻结（§3.2）** |

## 6. 为什么上面这些都不是「基准」

1. **测的是「不退化」，不是「效果」。** stress 的阈值留了 10–30 倍余量（§3.1 原文），只能抓
   平方级退化；它答不了「这次改动让任务成功率涨了还是跌了」。
2. **报的是测试项数，不是能力。** 项目进展一直以「内核 N 项测试」的形式记录（数量见 §4），
   而测试项数增长与能力增长没有必然关系——一个模块可以测得更细却一点没变强。
3. **没有对照。** 基准的定义是「同一任务集 + 同一环境 + 可重复 + 有前后对比」。现在任务集没有、
   环境没记录（§5 的数字大多没写机器与日期）、没有对比脚本。
4. **唯一做过对照的数字只存在于本地，且互相冲突。** §4 的表就是证据。

## 7. 首个基线该长什么样（协议）

这是给「下一个版本」的输入，与 `ROADMAP.md` 的 V1 是同一件事。协议分七条，每条都可判定：

| 维度 | 要求 |
|---|---|
| 任务集 | 参考场景 **R**（读取本地文件 + 计算）的 N 条任务；客观可判、只读无副作用、除模型外不联网 |
| 主指标 | **通过率**（通过 / 总数）——单一数字，别用复合分 |
| 次指标 | 失败分类计数（能力不足 / 预算不足 / 工具错误 / 权限拒绝 / 模型错误）、每任务轮数与 token |
| 环境 | 必须记录：commit、模型名与版本、机器、日期、是否联网、超时设置 |
| 冻结 | 首次测量后把基线值与测量环境写死；`web/budget.json` 的 `frozen_at` 同轮填上，之后只许改注释不许静默改阈值 |
| 执行 | 独立 marker（如 `-m eval`），**默认不跑**（真模型调用有成本与抖动）；提交前可一键跑；不进 CI 全量 |
| 对比 | 同一任务集、同一环境跑改动前后两次，**差值写入本文件**——这才叫「改动前后有可对比的评测数字」 |

**一个必须先定的取舍**（本文只列，不替决策者定）：真模型 vs 录制回放。真模型能测出 prompt /
工具描述差异，但有成本与抖动；回放便宜稳定但测不出模型行为变化。倾向「真模型 + 固定模型版本 +
少量任务」当基线、回放只做回归——这需要在动工前澄清（`AGENTS.md` §3 第零步）。

**本轮落地对照**（`benchmarks/`，commit `de3035c`；协议是上面那七条，这里是逐条现状）：

| 协议条目 | 现状 |
|---|---|
| 任务集 | **做到了**：两套 suite 共 21 条只读 case——v0（12 条，§9）与 v1（9 条难度 case，tier 3–5，§10.2）；全部客观可判、除模型外不联网 |
| 主指标 | **做到了**：`resolved` 是布尔量（全部确定性 grader 通过），无复合分 |
| 次指标 | **做到了**：轮数 / tokens / 工具调用 / 工具失败 / 拒绝 / 压缩 / 审批 / 墙钟 + 失败分类（能力 / 预算 / 模型 / 权限 / 基础设施 / 取消） |
| 环境 | **做到了**：`result.json` 与 `summary.txt` 记录 commit、模型名、日期、limits 与注入项（`overrides`）；机器仍只记在本文件里，未进结果文件 |
| 冻结 | **做了**：`web/budget.json` 的 `frozen_at` 已填并带测量环境（§3.2）；两套 suite 的基线与「跨 suite 不可比」的规则写进 §10.4 |
| 执行 | **做到了**：`-m eval` / `-m eval_smoke` 两个 marker，默认不进 `pytest`，不进 CI；另有 `--suite` / `--context-chars` 两个开关 |
| 对比 | **做到了**：同一 suite、同一 commit，只改压缩阈值跑了三档（§10.3）。**结论是没有量到机制，只量到抖动**——这比"没跑过对照"有用：它给出了一把尺子（full ±10% / core ±4%） |

**两处与协议的偏离，理由写在这里**：① 判定器不用 LLM judge——那只是把不可复现性从被测对象
转移到裁判身上，所以 v0.1 的 case 全部是确定性判定；② case 文件用 TOML（`tomllib` 是标准库）
而不是 YAML，省掉一个依赖。

## 8. 记录规范：以后每个数字都这么写

新增任何数字（性能或效果）时，按这个格式追加，缺字段就写「未记录」：

```
| 指标 | 值 | 命令 | 环境（commit / 模型 / 机器 / 日期） | 可复核 |
```

- **可复核**一栏只能填「是」（命令能重跑出同量级）或「否」（本地过程文档转引）——填「否」的
  数字不得用于任何架构或产品结论。
- 同一指标出现新值时，**改原行并注明变化**，不要在文末追加第二条（否则重演 §4 的口径冲突）。
- 阈值或预算类数字变动必须同时写「为什么改」。

## 9. 首个基线（AvidBench v0.1）

**这是什么**：AvidBench 的第一轮全量运行——12 条只读 case × 3 个变体（跨会话 case 只跑 `full`
的两种条件），共 **36 次真模型运行**。它是一次**测量**，不是门禁：通过率不决定任何退出码。

### 9.1 环境与命令

| 项 | 值 |
|---|---|
| 命令 | `uv run --env-file .env python -m benchmarks.run --out benchmarks/runs/baseline` |
| commit | `1ece312`（跑基线时的代码；此后只改过本文档，未改 case / fixture / runner） |
| 模型 | `deepseek/deepseek-v4.1-flash`（`AVID_MODEL`；base_url `https://api.commandcode.ai/provider/v1`） |
| 机器 | `LAPTOP-3M7941PJ`，WSL2（`Linux 6.6.87.2-microsoft-standard-WSL2`），32 vCPU，Python 3.12.3 |
| 日期 | 2026-09-19 09:23:44Z（UTC） |
| 上限 | 每条 case 的 `timeout_seconds` 240–300（逐条见 `benchmarks/cases/v0/*.toml`）。**历史上**还有 `max_rounds` 10–16，该机制已删除：轮数不是收敛判据，内核与评测都不再有轮数上限，「预算不足」现在只由墙钟超时产生 |
| 权限 | `auto_approve=True`（离线跑；因此「拒绝」恒为 0，**不代表**权限层没工作） |
| 记录 | `benchmarks/runs/baseline/`（**不入库**，`.gitignore`）：每次运行的 `result.json` / `trajectory.jsonl` / `answer.txt` |

两条入口都在同一环境复验过：CLI（上表，36 次运行，产出 §9.2 的数字）与 pytest 薄壳
（`uv run --env-file .env pytest -q -m eval_smoke -s` → 3 条 × 3 变体全部 `resolved`，
44 s）。首次跑 pytest 路径时踩到一个坑并已修掉：`tests/conftest.py` 的 autouse `model_env`
把 `AVID_API_KEY` 顶成 `test-key`，9 次运行全部 401 且失败态是 `llm_error`——它原本不在
「仪器错误」的断言里，于是**全红但绿**。修法两处：夹具对 `eval` 标记让开；断言把
`llm_error` 并入仪器错误。缺陷运行留在 `benchmarks/runs/20260919T092752Z-*/`（本地）作为记录。

### 9.2 数字

| arm | resolved | tokens 均值 | rounds 均值 | 墙钟均值 | 工具调用均值 | 失败分类 |
|---|---|---|---|---|---|---|
| `bare`（自写朴素循环 + 3 工具） | **11/11** | 3,826.7 | 3.3 | 5.3 s | 2.82 | — |
| `core`（真 `agent_loop`，同工具集） | **11/11** | 4,297.6 | 3.3 | 5.7 s | 2.64 | — |
| `full`（全工具与全机制） | **11/11** | 10,438.7 | 3.3 | 5.4 s | 2.45 | — |
| `full--phase1`（跨会话第一轮，未计分） | 未计分 | 5,954.0 | 2.0 | 3.3 s | — | — |
| `full--resume`（第二轮带会话历史） | **1/1** | 3,020.0 | 1.0 | 1.6 s | 0 | — |
| `full--fresh`（第二轮干净上下文） | **0/1** | 16,651.0 | 5.0 | 9.6 s | 4 | 能力 1 |

机制的触发合计（36 次运行）：`compactions` **0**、`todo_reminders` 10、`stop_nudges` 0、
`denials` 0、`tool_failures` 0、`approvals_requested` 0；36 次运行的 `workspace_pristine` **全为真**
（没有一次写入受判文件，只读约束成立）。

### 9.3 三条结论

1. **任务集偏易，成功率没有区分度。** 11 条非会话 case 上三个变体全部通过，而且**机制确实触发过**
   （TODO 提醒 10 次），结果仍然毫无差别——这不是「机制没用」，而是**这套任务测不出机制有没有用**
   （天花板效应）。要提高区分度只能加难度（更多失败点、更长上下文、更深依赖），不是继续加机制。
2. **成本差异是真实的，且方向与直觉相反。** `full` 的 token 是 `bare` 的 **2.7 倍**
   （10,438.7 / 3,826.7），但轮数（3.3）与墙钟（5.4 s vs 5.3 s）几乎相同，工具调用甚至略少
   （2.45 vs 2.82）。差额主要来自**每次请求都带上 14 个工具的定义**，不是「多跑了几轮」。
   以后比 token 效率必须把「schema 开销」与「额外推理」分开算——这是首轮基线给出的一条口径。
3. **唯一有区分度的对照是跨会话那一条。** `resume` 用 1 轮 / 3,020 token 直接答对；`fresh` 花
   5 轮 / 16,651 token 去工作区里翻找那个**根本不在工作区里**的约定（`find` → `cat POLICY.txt`
   → `ls -la` + `git log` → 再跑一次脚本），最后答 `code=8842`、缺前缀而失败。它量到的是
   **会话投影**的价值：把结论从「5 轮搜索」降到「1 轮复述」。

### 9.4 这次测量不能证明什么

- **不能证明 Task / Subagent 有没有增益**：各只有 1 条 case，且三个变体都通过。
- **不能证明压缩有没有用**：36 次运行里 `compactions = 0`，压缩管线**一次都没触发**——这套任务
  根本没有覆盖它的适用场景。
- **不代表生产路径**：runner 直连 `agent_loop`，不经 `svc.RunRegistry`。
- **不能与将来的数字直接比**，除非模型版本、机器与 limits 相同；改 case / fixture / runner 后
  必须重跑才能新增一行（§8 的记录规范）。

## 10. AvidBench v1：难度分层与一次单变量对照（2026-09-19）

### 10.1 环境与命令

| 项 | 值 |
|---|---|
| 基线命令 | `uv run --env-file .env python -m benchmarks.run --suite v1 --out benchmarks/runs/v1-baseline` |
| 对照命令 | 同上，加 `--variants core,full --context-chars 30000`（以及 `12000`） |
| commit | `de3035c`——三次运行是**同一个 commit**，对照只差一个命令行参数（这是能做单变量对照的前提） |
| 模型 / 机器 / 日期 | 同 §9.1（`deepseek/deepseek-v4.1-flash`；WSL2；2026-09-19 09:58–10:04Z） |
| 记录 | `benchmarks/runs/v1-baseline/`、`v1-tight/`、`v1-tight-12k/`（均不入库） |

### 10.2 v1 基线（9 条难度 case × 3 臂 = 27 次运行）

| arm | resolved | tokens 均值 | rounds 均值 | 墙钟均值 |
|---|---|---|---|---|
| `bare` | **9/9** | 5,852.1 | 4.1 | 6.9 s |
| `core` | **9/9** | 5,428.6 | 3.6 | 6.1 s |
| `full` | **9/9** | 13,184.0 | 3.9 | 7.2 s |

**加难度没有换来区分度。** 9 条 tier 3–5 的只读任务上，三个臂仍然全部通过。诊断来自 27 次运行的
轨迹，比"任务偏易"这句话精确得多：

- **94 次工具调用里 72 次是 `bash`**，`read_file` 只有 21 次、`glob` 1 次。模型用 `grep -r` /
  `awk` / `cat` 把"36 个文件""40 跳链"折叠成一两条命令——所以 rounds 中位数只有 3–4；
- 由此 **`compactions` 27 次里仍然是 0**：transcript 根本没长起来；
- `todo_reminders` 触发了 11 次（循环确实在提醒），结果仍无差别。

也就是说：**只读任务的难度天花板不由 case 的"步数"决定，而由"能不能一条 shell 折叠掉"决定。**
想在这套 harness 的机制上看出差别，要么换到可写任务（改代码 + 跑测试，shell 折叠不掉），要么
造出 shell 也压不平的上下文量。

### 10.3 单变量对照：只改压缩阈值

| 条件（`context_chars`） | arm | resolved | tokens 均值 | rounds 均值 | `compactions` |
|---|---|---|---|---|---|
| 默认 400,000 | core / full | 9/9 / 9/9 | 5,428.6 / 13,184.0 | 3.6 / 3.9 | 0 |
| 30,000 | core / full | 9/9 / 9/9 | 5,190.6 / 11,201.2 | 3.4 / 3.3 | 0 |
| 12,000 | core / full | 9/9 / 9/9 | 5,590.6 / 11,732.3 | 3.4 / 3.4 | 0 |

**这次对照没有量到压缩，它量到的是抖动。** 三档阈值下 `compactions` **全是 0**：45 次运行里
最大的 transcript 只有 **4,657 字符**（中位数 1,281），而默认阈值是 400,000——**差 86 倍**，
阈值根本没被触达。因此上表的 token 差异（core ±3.7%、full ±8.5%）不能归因于压缩。

两个用途：① 把"阈值该调多少"从猜测变成待定项——在本任务族上要咬住阈值需要降到 ~4k，而那个
规模下几乎每轮都会触发摘要调用，已经不是"调一档"，而是另一种配置；② 给出一把尺子：
**以后 `full` 的 token 差小于 ~±10%（或 core 小于 ~±4%）就不要当结论**。

对照本身的管线是通的：每次运行的 `result.json` 里 `overrides.context_chars` 都记着注入值，
`bare` 那一臂不记（它没有压缩机制，记了会凭空多出一个不存在的差异来源）——由
`tests/test_bench_runner.py` 钉住。

### 10.4 冻结

- **基线**：§9 的 v0 与本节 §10.2/§10.3 的 v1 各自绑定 suite / commit / 模型 / 机器 / limits；
  **跨 suite 的差值不可比**（任务集不是同一份）。
- **case 集**：`cases/v0` 与 `cases/v1` 都已有基线落盘，从此不再改；要改 case、fixture 或判定器，
  就新建 `cases/v2` 并各自记一行基线。`--suite` 是唯一选择器。
- **不设通过率门禁**：评测要真模型、有抖动、默认不跑；把某次运行的通过率写成 CI 阈值，等于把
  一次测量当成结论。留下的门禁只有"仪器可用"（跑不完、`error` / `llm_error` 态）。
- **前端阈值**：见 §3.2（同轮实测并冻结）。
