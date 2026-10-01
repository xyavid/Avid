# Avid

自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限、评测各层都掌握在自己手里，做到可替换、可调试、可度量。

**当前进度**：内核（模型调用、循环、工具、会话持久化、事件层）与**本地 Web 服务**
（FastAPI 路由、pydantic DTO、SSE 事件流、静态资源服务）都已跑通。
**前端已整体删除**（页面、契约种子、e2e 与 pnpm 工具链），待讨论定稿后从零重建
（见 `docs/status/CAPABILITIES.md` §11）。

正式文档在 `docs/`，收录标准见 `docs/README.md`；开发过程文档在 `dev/`，只留本地、不入库。协作约定见 `AGENTS.md`。

## 安装

```bash
uv sync
```

## 配置

```bash
cp .env.example .env
# 编辑 .env，至少填入 AVID_API_KEY 与 AVID_MODEL
```

| 变量 | 必填 | 说明 |
|---|---|---|
| `AVID_API_KEY` | 是 | 服务商签发的密钥 |
| `AVID_BASE_URL` | 否 | OpenAI 兼容接口根地址，默认 `https://api.openai.com/v1` |
| `AVID_MODEL` | 是 | 模型名，例如 `deepseek-chat`、`gpt-4o-mini` |

## 运行

单轮问答：

```bash
uv run --env-file .env avid "用一句话说明你是谁"
```

agent 循环（模型可自主调用工具）：

```bash
uv run --env-file .env avid --agent "读 pyproject.toml，告诉我项目名"
```

工具执行前过一道四层裁决（硬拒绝 → 危险命令 → 越界 → 常规规则），配合三档权限模式
（`--mode strict|workspace|system`，见 `docs/design/workspace-permission.md` 的决策表）：

- **硬拒绝**（`rm -rf /` 这类）三种模式一律不执行；
- **危险命令**（提权、递归删除、系统级包管理、`~/.ssh` 这类敏感路径等 15 类）三种模式
  一律问一次，按规范化命令原文记账，同一次运行内不再重复问；
- **越界**（目标在工作区之外）在 `strict` / `workspace` 问一次、`system` 放行；
- 区内常规操作在 `workspace` / `system` 免问。

提示写在 stderr；非交互场景加 `--yes` 跳过询问（**硬拒绝仍然生效**）。`subagent` 的审批
由父运行前传（子 agent 在别的线程跑），账本共用一本。

stdout 打印模型回复，stderr 打印逐轮 trace 与 token 用量。

## Web 界面

```bash
uv sync --extra web                              # 装 Web 依赖（FastAPI/uvicorn）
uv run --env-file .env avid web --port 8765      # API + SSE + 静态资源
# → http://127.0.0.1:8765
```

浏览器端目前没有页面：前端连同其构建工具链已整体删除，`avid web` 现在只提供
API 与 SSE；访问非 `/api` 路径会得到一条「前端尚未重建」的提示（HTTP 503）。

Web 服务的接口、SSE 消费规则与信任边界见 `docs/guide/web-ui.md`；新前端定稿后
在此补页面说明与交付形态。

## 开发

```bash
uv run pytest                                 # 内核与 API 全部测试，不联网
uv run ruff check src tests && uv run mypy    # 与 CI 同一套静态检查
```

前端（页面、契约种子、浏览器 e2e 与各项门禁）已整体删除；新的工具链与门禁待前端设计定稿后随新结构建立。
