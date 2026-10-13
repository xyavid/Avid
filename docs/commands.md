# 命令与举证

## 环境

```bash
uv sync && uv sync --extra web   # 内核依赖 / 追加 Web 依赖（fastapi·uvicorn·pydantic）
uv tool install --editable ".[web]"   # 装成命令：任何目录直接敲 `avid`（改代码立即生效）
pnpm -C web install               # 前端依赖
pnpm -C web run copy:dist         # 构建产物交付到 avid/web/static/（avid web 服务它）
```

**模型连接只认 BYOK 配置**：`~/.avid/models.json` + 0600 的 `~/.avid/secrets.json`，由界面「设置 → 模型」或手编文件维护（结构见 `providers/byok.py` 模块注释）；未配置时运行报「还没有模型配置」。安装、配置项与 CLI 全量参数见 `README.md`（英文）与 `README.zh-CN.md`（中文）。

## 日常运行

```bash
avid --agent "读 pyproject.toml，告诉我项目名"   # 单轮
avid web --port 8765                          # Web 服务
avid session search "词"                      # 按内容检索历史会话（本地索引，不调模型）
avid index check --fix                        # 校验会话索引，需要时补/修
```

删掉 `~/.avid/index` 也不会伤会话——JSONL 是唯一权威，索引是可丢的派生层（见 [kernel.md](kernel.md)）。

## 门禁

```bash
uv run pytest                                 # 全量（stress 件默认不跑，见 pyproject 的 addopts）
uv run pytest -m stress                       # 复杂度与长会话门禁（CI 有单独 job）
uv run ruff check avid tests && uv run mypy   # 与 CI 同一套静态检查
uv run pytest tests/test_web_boundaries.py tests/test_docs_contract.py -q   # 分层 + 文档门禁

pnpm -C web run verify                       # 前端门禁：typecheck + vitest + build + gate:size（体积预算）
```

## 举证要求

按 `AGENTS.md` §2 第三步逐条对验收标准给出证据：**可执行的命令 + 实际输出**，不接受「模块已完成」。同一条命令重跑要得到同样结论——这也是 E2E 产物必须落在仓库里、可被他人直接打开检查的原因。命令与被否掉的方案写进 `dev/plan/roadmap.md`。
