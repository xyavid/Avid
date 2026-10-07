# Avid

自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限各层都掌握在自己手里，做到可替换、可调试。

**当前进度**：内核（模型调用、循环、工具、会话持久化、事件层）与**本地 Web 服务**
（FastAPI 路由、pydantic DTO、SSE 事件流、静态资源服务）都已跑通。
浏览器界面已随阶段 33 重建（`web/`，React 18 + Vite：纸本视觉对话界面、会话与工作区
管理、设置）。

仓库内没有 docs/ 目录：现状以代码与模块注释为准，协作约定见 `AGENTS.md`；
开发过程文档在 `dev/`，只留本地、不入库。

## 安装

```bash
uv sync
```

## 配置

模型连接**只认 BYOK 配置**（不读 `.env` 里的模型变量）：在界面
「设置 → 模型」里维护，或手编文件。每份提供商配置 = 协议（OpenAI 兼容 / Responses /
Anthropic / Ollama）+ 接口地址 + 密钥引用 + 模型与能力声明；chat 槽位绑定一个
`providerId/modelId` 作为主对话模型。规则：

- **两份文件**：`~/.avid/models.json`（providers + bindings，只存 `secretRef` 引用，
  可以随意备份分享）与 `~/.avid/secrets.json`（明文密钥，0600，原子写）；
- **没有配置就跑不了**：未绑定 chat 槽位时发送消息会报「还没有模型配置」，文案给出
  可执行的修复步骤；保存后对下一条消息立即生效，无需重启；
- **连通校验**：每个模型可跑「最小对话 + 工具冒烟」两步探测，能在配置阶段筛掉
  「能聊天、不能调工具」的模型；
- 「按运行换模型」的候选自动带上 BYOK 模型（`providerId/modelId`）。

也可以直接手编 `~/.avid/models.json`（providers + bindings，结构见
`avid/providers/byok.py` 模块注释）；写坏了会报可执行的修复文案，绝不静默回落。

### 环境变量（全部可选，日常运行不需要）

模型连接**不在环境变量里**配。剩下的都是运行期开关，按需 `export` 即可；
想固定下来就自己写一个 `.env`，再给 `uv run` 加 `--env-file .env`（仓库不提供
样例文件）：

| 变量 | 必填 | 说明 |
|---|---|---|
| `AVID_MAX_PARALLEL_TOOL_CALLS` | 否 | 一步内并行工具调用上限，默认 10，硬上限 32 |
| `AVID_MODEL_INFO` | 否 | 设 `off` 关闭「向 provider 问模型窗口」的探测 |
| `AVID_HOME` | 否 | 用户级目录（会话注册表、审计）改到别处，默认 `~/.avid` |
| `AVID_AUDIT_DIR` | 否 | 审计 JSONL 单独落一个目录 |
| `AVID_SANDBOX_BIN` | 否 | 换一个 bwrap 可执行文件（诊断/打包用） |
| `AVID_ALLOWED_HOSTS` | 否 | Web 服务额外信任的主机名（LAN 部署） |

## 运行

先把它装成一条命令（推荐；装完在任何目录直接敲 `avid`，改代码立即生效）：

```bash
uv tool install --editable ".[web]"   # 内核 + Web 依赖；只跑 CLI 可去掉 [web]
```

交互会话（默认续接最近会话）：

```bash
avid
# avid> 读 pyproject.toml，告诉我项目名
# avid> /compact          ← 压缩当前会话历史（保留最近轮，更早部分摘要化）
# avid> /rewind           ← 回滚最近一次用户输入（对话指针回移，文件恢复到该点）
# avid> /code-review      ← 载入技能全文（写入会话，下一轮模型即见）
# Ctrl-D 退出；--new-session 起新会话，--session ID 续指定会话
```

单轮问答（带问题即单轮，不进入交互）：

```bash
avid "用一句话说明你是谁"
avid --agent "读 pyproject.toml，告诉我项目名"
```

不想装成命令时，等价写法是 `uv run --directory <仓库> avid …`；已经在仓库目录里就是
`uv run avid …`。改过依赖（pyproject）后重跑一次上面的 `uv tool install` 即可。

**不需要 `.env`**：模型走 BYOK 文件（`~/.avid/models.json` + `secrets.json`），
环境变量只有可选开关（见上一节）。没配模型时启动会直接告诉你「还没有模型配置」。

工具执行前过一道轻量裁决（阶段 51，决策事实见 `avid/security/engine.py` 与
`permission.py` 的注释）：

- **默认**：一切直接执行——sudo、docker、网络命令、工作区外的读写都不再询问；
  只有**毁灭级命令**（`rm -rf /`、`mkfs`、写块设备、fork 炸弹、关机重启、
  `chmod -R /`）触发确认，终端里**连问两次**（Web 是审批卡两步）。
- **凭据拒读**：`~/.ssh`、`~/.aws`、`/etc/shadow`、`*.pem` 这类宿主凭据在任何形态下
  都拒（凭据进上下文不可撤回）——它是唯一硬拒，连完全访问也不放行。
- **完全访问**（`--allow-full-access`；Web 用 `full_access_ack`）：连毁灭级确认也跳过、
  关沙箱、不滤环境变量。没有「工作区默认权限」这类持久设置：授权只属于某一次运行。

区外写不再问人，而是自动记账并把目标挂进沙箱（bwrap 只挂已存在的路径）。沙箱按平台取
最强可用机制：Linux 用 bubblewrap（bwrap）做内核级隔离（网络不隔离，网络命令直接跑）；
Windows/macOS 没有可用后端时不上锁，靠毁灭级确认与事后审计兜底（Windows 上 `bash`
工具经 PowerShell 运行）。提示写在 stderr；非交互场景加 `--yes` 代答毁灭级确认
（**凭据拒读仍然生效**）。`subagent` 的确认由父运行前传（子 agent 在别的线程跑），
账本共用一本：同一条毁灭级命令答过一次，本轮不再问。

stdout 打印模型回复，stderr 打印逐轮 trace 与 token 用量。

## Web 界面

```bash
uv sync --extra web                              # 装 Web 依赖（FastAPI/uvicorn）
pnpm -C web install && pnpm -C web run copy:dist # 前端产物交付到 avid/web/static/
avid web --port 8765                             # API + SSE + 静态资源
# → http://127.0.0.1:8765
```

（没装成命令就用 `uv run --directory <仓库> avid web --port 8765`；`avid web` 只在
`uv sync --extra web` 之后可用，缺 FastAPI 时会提示装 `web` extra。）

浏览器界面（阶段 33 重建）：纸本视觉的对话页——时间线（思考块 / 工具卡 / 消息动作行 /
用量卡）、发送与流式、会话管理（新建 / 重命名 / 删除）、工作区分组与选择、权限与按运行的
模型选择、设置面板；组件墙在 `?gallery=1`。没有产物时访问非 `/api` 路径会得到
HTTP 503 `static_missing`。

接口面（端点 / 事件 / 信任边界）看 `avid/web/routes/` 与 `avid/web/schemas.py` 的
模块注释。

## 开发

```bash
uv run pytest                                 # 内核与 API 全部测试，不联网
uv run ruff check avid tests && uv run mypy   # 与 CI 同一套静态检查
pnpm -C web run verify                        # 前端：typecheck + vitest + build + gate:size
```

前端的对账门禁（wire / event 契约、模式词表、A12）在 pytest 里随内核一起跑；体积门禁
`gate:size` 对 `web/budget.json` 的冻结预算。架构门禁（A1–A13）在
`tests/test_web_boundaries.py`。浏览器 e2e、a11y 与视觉回归尚未重建。
