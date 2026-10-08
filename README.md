<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/src/assets/avid-logo-dark.svg">
    <img src="web/src/assets/avid-logo-light.svg" alt="Avid" width="326">
  </picture>
</p>

# Avid

> 自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限各层都掌握在自己手里，做到可替换、可调试。CLI 与本地 Web 服务是内核的两个平级接线点。

[协作约定与架构判据](AGENTS.md) · [功能](#功能) · [快速开始](#快速开始) · [配置](#配置) · [权限与沙箱](#权限与沙箱) · [文档](#文档) · [开发](#开发)

**状态**：内核（模型调用 → 循环 → 内置工具 + stdio MCP → 权限 → hook 四事件 → 技能 →
上下文压缩 → 会话持久化）与本地 Web 服务端到端可用；浏览器界面已随阶段 33 重建。

## 功能

- **内核** —— 一个循环（`avid/agent/run.py`）+ 一张上下文声明表：模型调用、工具批量执行、终止判定、压缩、落库各归其位；跨包依赖由门禁钉住（`tests/test_web_boundaries.py`，A1–A13）。
- **模型适配** —— BYOK 走四家协议（OpenAI 兼容 / Responses / Anthropic / Ollama），对循环返回**同形** `Turn`；换模型只动 `avid/providers/` 一层。
- **工具** —— 8 个内置工具（读写改文件、glob、shell、todo、技能、子智能体）+ 工作区声明的 stdio MCP 工具；`@tool` 是唯一声明点，其余表格全部派生。
- **权限** —— 默认直接执行；毁灭级命令确认（CLI 连问两次、Web 审批卡两步）、宿主凭据硬拒读、跨平台沙箱、审计 JSONL。
- **上下文与记忆** —— 分区装配（指令 / 环境 / 工作区约定 / 技能 / 计划 / 运行状态）；压缩成**结构化检查点**：首次压缩新建，其后按仓库现状更新，事实与假设分开记（`avid/agent/compaction.py`）。
- **会话** —— 条目树 + 分支 + 变更线，JSONL 是磁盘上的真相；`/rewind` 把对话指针与文件一起回滚。
- **界面** —— 纸本视觉的本地 Web 界面（React 18 + Vite）：流式时间线、工具卡差异视图、右列面板、设置；组件墙在 `?gallery=1`。

## 快速开始

```bash
uv sync --extra web                   # 内核 + Web 依赖（只跑 CLI 就别带 [web]）
uv tool install --editable ".[web]"   # 装成命令：任何目录直接敲 `avid`，改代码立即生效
avid --agent "读 pyproject.toml，告诉我项目名"
```

交互会话（不带问题即进入，默认续接最近会话）：

```bash
avid
# avid> /compact      ← 压缩当前会话历史（保留最近轮，更早部分压成检查点）
# avid> /rewind       ← 回滚最近一次输入（对话指针回移，文件恢复到该点）
# avid> /code-review  ← 载入技能全文（/<技能名> 都行，写入会话，下一轮模型即见）
# Ctrl-D 退出
```

浏览器界面：

```bash
pnpm -C web install && pnpm -C web run copy:dist   # 前端产物交付到 avid/web/static/
avid web --port 8765                               # → http://127.0.0.1:8765
```

没装成命令时的等价写法是 `uv run avid …`。`avid web` 需要 `uv sync --extra web`，缺
FastAPI 时会提示装 `web` extra；没有产物时访问非 `/api` 路径返回 HTTP 503
`static_missing`。CLI 全量参数见 `avid --help`（`--session` / `--new-session` /
`--workspace` / `--yes` / `--allow-full-access` / `--list-sessions` …），工作区管理见
`avid workspace --help`。

## 配置

- **工作区约定**：`AGENTS.md`（放在工作区根目录，进 system 提示的常驻块，上限 16,000 字符）。
- **用户配置**：`~/.avid/models.json`（providers + bindings，只存 `secretRef` 引用，可随意备份分享）+ `~/.avid/secrets.json`（明文密钥，0600，原子写）。
- **环境变量**：全部可选，日常运行不需要（见下表）。

**模型连接只认 BYOK 配置**：由界面「设置 → 模型」维护，或手编上面两份文件（结构见
`avid/providers/byok.py` 的模块注释）。每份提供商配置 = 协议 + 接口地址 + 密钥引用 +
模型与能力声明；chat 槽位绑定一个 `providerId/modelId` 作为主对话模型。未绑定就发送消息
会报「还没有模型配置」，文案给出可执行的修复步骤；保存后对下一条消息立即生效，无需重启。
每个模型可跑「最小对话 + 工具冒烟」两步连通校验，能在配置阶段筛掉「能聊天、不能调工具」
的模型。

| 环境变量 | 说明 |
|---|---|
| `AVID_MAX_PARALLEL_TOOL_CALLS` | 一步内并行工具调用上限，默认 10，硬上限 32 |
| `AVID_MODEL_INFO` | 设 `off` 关闭「向 provider 问模型窗口」的探测 |
| `AVID_HOME` | 用户级目录（会话注册表、审计）改到别处，默认 `~/.avid` |
| `AVID_AUDIT_DIR` | 审计 JSONL 单独落一个目录 |
| `AVID_SANDBOX_BIN` | 换一个 bwrap 可执行文件（诊断 / 打包用） |
| `AVID_ALLOWED_HOSTS` | Web 服务额外信任的主机名（LAN 部署） |

想固定下来就自己写一个 `.env`，再给 `uv run` 加 `--env-file .env`（仓库不提供样例文件）。
**模型连接不在环境变量里配**。

## 权限与沙箱

工具执行前过一道轻量裁决（事实与文案见 `avid/security/engine.py`、`permission.py`）：

- **默认**：一切直接执行——sudo、docker、网络命令、工作区外的读写都不再询问；只有
  **毁灭级命令**（`rm -rf /`、`mkfs`、写块设备、fork 炸弹、关机重启、`chmod -R /`）
  触发确认。非交互场景加 `--yes` 代答（**凭据拒读仍然生效**）。
- **凭据拒读**：`~/.ssh`、`~/.aws`、`/etc/shadow`、`*.pem` 这类宿主凭据在任何形态下都
  拒——它是唯一硬拒，连完全访问也不放行（凭据进上下文不可撤回）。
- **完全访问**（`--allow-full-access`，Web 用 `full_access_ack`）：跳过毁灭级确认、关沙箱、
  不滤环境变量。没有「工作区默认权限」这类持久设置：授权只属于某一次运行。
- **沙箱**：区外写自动记账并把目标挂进 bwrap（只挂已存在的路径）；Linux 用 bubblewrap
  做内核级隔离（网络不隔离），Windows / macOS 没有可用后端时靠毁灭级确认与事后审计兜底
  （Windows 上 `bash` 工具经 PowerShell 运行）。

子智能体的确认由父运行前传、账本共用一本：同一条毁灭级命令答过一次，本轮不再问。
stdout 打印模型回复，stderr 打印逐轮 trace 与 token 用量。

## 文档

仓库内**没有 docs/ 目录：代码与模块注释是唯一现状**。

- [协作约定与架构判据](AGENTS.md) —— 提交规范、阶段流程、十二组架构检查点、测试约定，以及数据流与关键子系统表。
- 技能的运行期契约：`skills/*/SKILL.md`。
- 门禁即文档：分层边界 `tests/test_web_boundaries.py`（A1–A13）、线格式 `tests/test_wire_contract.py`、事件名单 `tests/test_event_contract.py`、压缩与检查点 `tests/test_compact.py`。
- 开发过程文档（计划 / 诊断 / 阶段出图）在 `dev/`，只留本地、不入库。

## 开发

```bash
uv sync --extra web                           # 装依赖（内核 + Web + dev 组）
uv run pytest                                 # 内核与 API 全部测试，不联网
uv run pytest -m stress                       # 复杂度与长会话门禁（默认不跑）
uv run ruff check avid tests && uv run mypy   # 与 CI 同一套静态检查

pnpm -C web install                           # 前端依赖
pnpm -C web run verify                        # typecheck + vitest + build + gate:size
```

改前端后把产物交付到服务端目录：`pnpm -C web run copy:dist`。浏览器 e2e、a11y 与视觉
回归尚未重建。开发约定（提交格式、测试纪律、注释纪律）见 [AGENTS.md](AGENTS.md)。

## 许可

**许可尚未声明**：仓库里没有 `LICENSE` 文件，`pyproject.toml` 也没有 `license` 字段——
在补上之前默认保留全部权利。要授权使用时，两处一起补。
