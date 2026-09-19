# Avid 性能与效果基准

本文件记录**性能与效果的基准**：有什么数字、缺什么数字、首个基线该怎么建。

**当前结论（一句话）**：**基准尚不存在。** 仓库里有一个「防复杂度退化」的门禁和一组体积上限，
但没有任何任务成功率、没有前后对比机制、体积预算也未冻结。

**更新时机**：基准首次建立、阈值冻结、或跑出一轮新基线时。每次记录必须写清测量环境与命令。

**写作方式说明**：本文件写作时**没有运行任何测试或测量**（`pytest` / `pnpm` / `gate-size` 都没跑），
因此正文里每个数字都标了出处，并区分「仓库内可复核」与「本地记录不可复核」。这不是偷懒——
「当前收集数无法从任何文件确定」本身就是本文件第一条结论的证据（§4）。

---

## 1. 结论

| 问题 | 答案 |
|---|---|
| 有任务成功率 / 通过率吗？ | 没有 |
| 有性能基线（当前值 + 可重复的测量命令）吗？ | 没有 |
| 有「改动前后对比」机制吗？ | 没有 |
| 有防退化的复杂度门禁吗？ | **有**：3 条 stress 用例（§3.1） |
| 有体积/资源上限吗？ | **有**，但是「起点值」不是基线：`web/budget.json` 的 `frozen_at` 是 `null`（§3.2） |
| 有测试规模的数字吗？ | 有静态计数（§3.3）；但它是**测试项数**，不是能力或效果指标 |

一句话概括现状：**仓库能告诉你「这次改动有没有让某条路径从线性退化成平方级」，不能告诉你
「这一版是不是比上一版更会干活」。**

## 2. 「不存在」的清单与证明

| 要找的东西 | 命令 | 实际输出（本文件写作时执行） |
|---|---|---|
| 评测 / 基准目录 | `ls -d eval evals benchmarks bench tests/eval` | 五个全部 `No such file or directory` |
| 评测 / 基准文件 | `find src tests web/src skills docs -iname '*eval*' -o -iname '*bench*' -o -iname '*baseline*'` | 无输出（`dev/architecture/phase-0-baseline.svg` 是架构图，不是评测基线） |
| 通过率 / 准确率 / 任务集 | `grep -rniE '通过率\|pass rate\|pass@\|准确率\|accuracy\|任务集' src tests --include='*.py'` | 零命中 |
| 成本 / token 台账 | `grep -rn 'usage\|total_tokens' src/avid --include='*.py'` | 只有运行内计数；`src/avid/session/types.py:149` 自述「Avid 的 token 用量目前不落盘」；无成本/计价任何代码 |
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

### 3.2 前端体积预算：`web/budget.json`（**未冻结**）

| 键 | 值 | 含义 |
|---|---|---|
| `entry_gzip_bytes` | 460800 | 首屏入口 JS 逐块 gzip 上限 |
| `chunk_gzip_bytes` | 358400 | 单个异步块上限 |
| `exempt_gzip_bytes` | 204800 | 豁免上限 |
| `font_bytes` | 61440 | 字体体积上限 |
| `texture_bytes` | 32768 | 位图纹理上限 |
| `frozen_at` | `null` | **未冻结**——注释自称「起点值：首次实测前用来拦住明显失控的引入」 |
| `EXEMPT` | `[]` | 空 |

同一指标的历次记录（**全部是本地过程文档里的数，未经本文件复核**，逐条出处见 §5）：
`180 KB ≤ 450 KB` → `182 KB ≤ 450 KB` → `183,197 B ≤ 460,800 B` → `184,111 B / 460,800` →
`184,311 B / 460,800`。五个版本，阈值也变过一次（450 KB → 460,800 B）。这就是「未冻结」的代价：
无法判断某次变化是涨了还是只是换了个测量口径。

### 3.3 测试规模（**静态计数**，不是 pytest 收集数）

| 指标 | 值 | 怎么数的 |
|---|---|---|
| 内核测试函数 | **627** 个，分布在 **36** 个 `tests/test_*.py` | `grep -h '^def test_' tests/*.py \| wc -l` |
| `parametrize` | **23** 处，分布在 **11** 个文件 | `grep -c parametrize` 逐个文件求和 |
| 内核测试代码量 | 11,862 行（`tests/*.py`） | `wc -l tests/*.py` |
| 前端 vitest | **74** 条，10 个文件（全在 `__tests__/` 下） | `^\s*(it\|test)\(` 计数 |
| 浏览器 e2e | **37** 条 `test(`，9 个 spec | 逐文件计数（layout 10 / interaction 8 / messages 7 / routes 4 / workspaces 3 / conversation 2 / branches 1 / smoke 1 / streaming 1） |
| `skip` / `xfail` / `skipif` | **0** | `grep` 零命中——没有靠跳过兜绿的用例 |

**口径警告**：627 是**函数数**，pytest 的收集数会被 `parametrize` 展开（例如
`test_session_conformance` 的 2 个函数 × 2 个后端 × 每个 case 会展开成几十项）。本文件
**不给当前收集数**——因为从仓库里无法确定它（§4）。测试项数增长在任何情况下都**不能**当作
能力或性能指标。

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
| 内核测试规模 | `822 passed + 3 deselected`；`869`；`863`；`844`；`835`；`832`；`711` | `dev/review/fix-progress.md:103`；`dev/plan/roadmap.md` 各阶段条目 | 三处口径不同（收集项 vs 函数 vs 不同时点）。**首个基线必须重新收集并只认一个口径** |
| 内核测试耗时 | `711 tests in 8.99s` | `dev/review/architecture-review.md:7`、`dev/review/tests.md:14` | 仅历史值；本机与 CI 环境不同，不可比 |
| 浏览器 e2e | `40 项全通过`、`38`、`36`、`35`；静态 **37** | `dev/plan/roadmap.md:192,179,169,150`；静态计数见 §3.3 | 静态与记录不一致（差额未解释）。以**重测**为准 |
| 首屏 JS gzip | 5 个值（见 §3.2） | `dev/review/*`、`dev/plan/roadmap.md` | 阈值变过一次，不可直接比 |

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
| 首屏 JS gzip | 184,311 B / 460,800 | `dev/review/fix-progress.md`（P2-8） |

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
| 环境 | 必须记录：commit、模型名与版本、机器、日期、是否联网、超时与轮数上限设置 |
| 冻结 | 首次测量后把基线值与测量环境写死；`web/budget.json` 的 `frozen_at` 同轮填上，之后只许改注释不许静默改阈值 |
| 执行 | 独立 marker（如 `-m eval`），**默认不跑**（真模型调用有成本与抖动）；提交前可一键跑；不进 CI 全量 |
| 对比 | 同一任务集、同一环境跑改动前后两次，**差值写入本文件**——这才叫「改动前后有可对比的评测数字」 |

**一个必须先定的取舍**（本文只列，不替决策者定）：真模型 vs 录制回放。真模型能测出 prompt /
工具描述差异，但有成本与抖动；回放便宜稳定但测不出模型行为变化。倾向「真模型 + 固定模型版本 +
少量任务」当基线、回放只做回归——这需要在动工前澄清（`AGENTS.md` §3 第零步）。

## 8. 记录规范：以后每个数字都这么写

新增任何数字（性能或效果）时，按这个格式追加，缺字段就写「未记录」：

```
| 指标 | 值 | 命令 | 环境（commit / 模型 / 机器 / 日期） | 可复核 |
```

- **可复核**一栏只能填「是」（命令能重跑出同量级）或「否」（本地过程文档转引）——填「否」的
  数字不得用于任何架构或产品结论。
- 同一指标出现新值时，**改原行并注明变化**，不要在文末追加第二条（否则重演 §4 的口径冲突）。
- 阈值或预算类数字变动必须同时写「为什么改」。
