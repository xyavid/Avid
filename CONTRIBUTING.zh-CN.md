# 贡献指南

给要改这个仓库的人。Avid 是自建的 agent 运行时：内核 Python 3.12（`uv` 管理，运行期只依赖 `httpx`），前端 `web/`（React 18 + Vite + pnpm）。

[English](CONTRIBUTING.md) · 中文

## 1. 环境与门禁

```bash
uv sync && uv sync --extra web       # 内核依赖 / 追加 Web 依赖（fastapi·uvicorn·pydantic）
pnpm -C web install                  # 前端依赖

uv run pytest                        # 全量（stress 件默认不跑，见 pyproject 的 addopts）
uv run pytest -m stress              # 复杂度与长会话门禁（CI 单独 job）
uv run ruff check avid tests && uv run mypy        # 静态检查（与 CI 同一套）
pnpm -C web run verify               # 前端：typecheck + vitest + build + 体积门禁
```

提 PR 前这几条要全绿。

## 2. 提交规范

每完成一个可验证的小步提交一次；同一件事不攒成一个大提交。

```
<type>(<scope>): <简述>

<正文：为什么这样改>
```

- **type** 取 `feat` / `fix` / `refactor` / `test` / `docs` / `chore` / `perf`。
- **scope** 写受影响的模块名，如 `loop`、`tools`、`context`、`perm`。
- **简述**用中文祈使句，说明"做了什么"，不超过 50 字，结尾不加句号。
- **正文**只在"为什么"无法从 diff 看出时写；补原因、取舍与被否掉的方案。
- **粒度**一个提交一件事；提交后项目保持可运行。

## 3. 注释纪律

- **注释用英文**，只写代码表达不了的东西。
- 留下：数字阈值、失败语义、跨模块不变量、外部契约、「这是唯一一处」的单点声明。
- 长度：模块 docstring ≤ 2 句（这个模块拥有什么 + 必要时一条不变量），函数与类 ≤ 1 句（名字已说明白的直接删），行内注释 ≤ 1 行。
- 不写：历史沿革、与其它项目的比较、设计过程复盘、举例、表格、分点列表。
- 中文只留在**面向人与模型的字符串**里（界面文案、错误消息、prompt 文本、日志）。
- 改代码必须同步改注释；发现注释说谎，当场修。

## 4. 测试约定

- **单元测试先写**：要写单元测试就在实现之前写；实现完成后补的不算数。
- **E2E 是默认测试手段**：复杂功能一律用 E2E 验证真的跑通，不用单元测试代替。E2E 末尾必须产出一个**可重复的产物**——同一条命令重跑得到同样结论，产物落在仓库里、可被他人直接打开检查。
- **隔离测试先列失败清单**：要对某个系统做隔离测试时，先把「它可能怎么坏」逐条列全，再写代码。
- 开发期间只跑与改动直接相关的最小验证，全套留到提 PR 前跑一次。

## 5. 运行期依赖

内核运行期只许 `httpx` 一个第三方依赖（`tests/test_runtime_deps.py` 拦）；web 栈在 `[web]` extra 里，CLI-only 安装不会拉进来。要扩权（新增依赖或提权），就在 `pyproject.toml`、本文件与该门禁里**显式承认**这次扩权。

## 现状与约定在哪

- 安装、配置项与 CLI 全量参数见 `README.md` / `README.zh-CN.md`。
- 项目自身的架构就在代码里：模块 docstring 说清每个模块拥有什么，`tests/test_web_boundaries.py` 钉住跨包依赖方向，`tests/test_wire_contract.py` / `test_event_contract.py` 钉住前端契约。
