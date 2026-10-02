# Avid

自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限、评测各层都掌握在自己手里，做到可替换、可调试、可度量。

**当前进度**：内核（模型调用、循环、工具、会话持久化、事件层）与**本地 Web 服务**
（FastAPI 路由、pydantic DTO、SSE 事件流、静态资源服务）都已跑通。
浏览器界面已随阶段 33 重建（`web/`，React 18 + Vite：纸本视觉对话界面、会话与工作区
管理、设置；分支 `refactor/web-hana-ui`，见 `docs/status/CAPABILITIES.md` §11）。

正式文档在 `docs/`，收录标准见 `docs/README.md`；开发过程文档在 `dev/`，只留本地、不入库。协作约定见 `AGENTS.md`。

## 安装

```bash
uv sync
```

## 配置

模型连接**只认 BYOK 配置**（阶段 34b 起，不再读 `.env` 里的模型变量）：在界面
「设置 → 模型」里维护，或手编文件。每份提供商配置 = 协议（OpenAI 兼容 / Anthropic /
Gemini / Ollama）+ 接口地址 + 密钥引用 + 模型与能力声明；chat 槽位绑定一个
`providerId/modelId` 作为主对话模型。规则：

- **两份文件**：`~/.avid/models.json`（providers + bindings，只存 `secretRef` 引用，
  可以随意备份分享）与 `~/.avid/secrets.json`（明文密钥，0600，原子写）；
- **没有配置就跑不了**：未绑定 chat 槽位时发送消息会报「还没有模型配置」，文案给出
  可执行的修复步骤；保存后对下一条消息立即生效，无需重启；
- **连通校验**：每个模型可跑「最小对话 + 工具冒烟」两步探测，能在配置阶段筛掉
  「能聊天、不能调工具」的模型；
- 「按运行换模型」的候选自动带上 BYOK 模型（`providerId/modelId`）。

也可以直接手编 `~/.avid/models.json`（providers + bindings，结构见
`src/avid/ai/byok.py` 模块注释）；写坏了会报可执行的修复文案，绝不静默回落。

### 环境变量（只剩旁路凭据与运行期开关）

```bash
cp .env.example .env   # 按需填入；模型连接不在这里配
```

| 变量 | 必填 | 说明 |
|---|---|---|
| `TAVILY_API_KEY` | 否 | `web_search` 工具的检索凭据；缺省只有该工具失败关闭 |
| `AVID_MAX_PARALLEL_TOOL_CALLS` | 否 | 一步内并行工具调用上限，默认 10，硬上限 32 |
| `AVID_MODEL_INFO` | 否 | 设 `off` 关闭「向 provider 问模型窗口」的探测 |

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
（`--permission manual|auto|full`，三轴预设见 `docs/design/workspace-permission.md` 的决策表）：

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
pnpm -C web install && pnpm -C web run copy:dist # 前端产物交付到 src/avid/web/static/
uv run --env-file .env avid web --port 8765      # API + SSE + 静态资源
# → http://127.0.0.1:8765
```

浏览器界面（阶段 33 重建）：纸本视觉的对话页——时间线（思考块 / 工具卡 / 消息动作行 /
用量卡）、发送与流式、会话管理（新建 / 重命名 / 删除）、工作区分组与选择、权限与按运行的
模型选择、设置面板；组件墙在 `?gallery=1`。没有产物时访问非 `/api` 路径会得到
HTTP 503 `static_missing`。

Web 服务的接口、SSE 消费规则与信任边界见 `docs/guide/web-ui.md`。

## 开发

```bash
uv run pytest                                 # 内核与 API 全部测试，不联网
uv run ruff check src tests && uv run mypy    # 与 CI 同一套静态检查
pnpm -C web run verify                        # 前端：typecheck + vitest + build + gate:size
```

前端的对账门禁（wire / event 契约、模式词表、A12）在 pytest 里随内核一起跑；体积门禁
`gate:size` 对 `web/budget.json` 的冻结预算。浏览器 e2e、a11y 与视觉回归尚未重建。
