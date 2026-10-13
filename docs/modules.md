# 子系统与入口

## 子系统

| 子系统 | 位置 | 职责 |
|---|---|---|
| Agent 核心 | `agent/` | 一次运行的生命周期与上下文策略：`run`（唯一循环）、`spec` 运行输入收口、`state.RunState` 全部可变状态、`context` 上下文装配（`CONTEXT_MAP` 声明表）、`compaction` 压缩通路、`stop` 终止路径（`StopReason`）、`execution` 工具协议、`transcript` 消息唯一所有者、`events` 事件名单点、`hooks` 默认回调、`todo` / `prompt` / `skills` |
| 模型适配 | `providers/` | 换模型只动这一层：`openai_compat` / `anthropic` / `responses` 三个实现 + `__init__.py` 注册表、`transport` 退避重试、`protocol` 共享词表、`client`、`usage` usage 归一、`byok` 模型配置、`verify` 连通校验、`reasoning` 思考记录与回传判定 |
| 安全 | `security/` | 毁灭级名单、阈值与文案的高频变化集中地：`action` 归一化与风险分类、`engine` 裁决（默认直接跑；毁灭级问一次；凭据硬拒；full 模式跳过询问）、`sandbox` bwrap、`audit`、`permission` 唯一装配点、`userdirs`（用户级目录 + 设置文件 + 会话目录解析）、`command_parse`（bash 只读判定） |
| 会话 | `session/` | 磁盘上的会话真相：条目树 + 值 + 分支 + 变更线，`memory` 与 `jsonl` 两后端共用一套一致性用例，`recorder` 是唯一写入者 |
| 会话索引 | `avid/index/` | JSONL 之上的派生查询层，可删可重建：`db`（连接 + user_version 迁移）、`scanner`（字节→行→记录，带行偏移）、`extract`（哪段文本进 search_text）、`writer`（条目与游标同事务）、`indexer`（发现 / 增量 / 补齐 / 重建 + 通知队列）、`queries`（读侧与 FTS 检索）、`check`（校验与修） |
| 应用服务 | `services/` | 内核的第二个调用方：`runs` 运行注册表与重放缓冲、`approvals` 待决表、`inbox` 补充输入表、`sessions` 读视图、`workspaces`、`workspace_registry`（含会话目录的 `sessions_root`）、`session_migration` 旧布局一次性搬迁、`picker` |
| 附件 | `attachments.py` | 根级叶子，stdlib-only：图片分块的形状 / 判型 / 上限 / 文本渲染与成本口径 / 线格式 ref 的唯一处；字节内联在会话条目里 |
| 传输适配 | `avid/web/` | FastAPI 路由 + pydantic DTO + SSE 编帧 + 静态资源；线格式的唯一所有者 |
| 工具 | `agent/tools/` | 12 个内置工具 + 工作区声明的 MCP 工具：`registry` 单点声明、`files` / `search` / `shell` / `subagent` / `interaction` / `skill` / `mcp` / `web`（联网，`extract` 是它的 HTML→文本叶子）、`safety`（并发判定）、`validate` 参数校验 |
| 工作区 | `workspaces.py` | 用户级注册表（`~/.avid/workspaces.json`，是索引不是权威） |
| 前端 | `web/` | 浏览器侧全部代码（React 18 + Vite + pnpm + TypeScript）；细节见 [web.md](web.md) |

## 入口点

| 入口 | 位置 |
|---|---|
| CLI | `avid/cli.py`：无参数进交互会话（`/compact`、`/rewind` 与 `/<技能名>`，解析单点在 `agent/commands.py`）；带问题为单轮 / `--agent` / `--session` / `avid workspace` / `avid session dir\|migrate\|search` / `avid index check\|rebuild` / `avid web` |
| Web 服务 | `avid/web/app.py`（FastAPI）；接口面在 `avid/web/routes/`，DTO 在 `avid/web/schemas.py`；`GET /api/search` 是唯一读索引的端点 |
| 模块入口 | `avid/__main__.py`（`python -m avid`） |
| 测试 | `tests/` 镜像 `avid/` 结构；`tests/test_web_boundaries.py` 是分层门禁，`tests/test_runtime_deps.py` 钉住运行期依赖 |

## 技能（skills/）

`SKILL.md` 是运行期契约：frontmatter 声明 `name` / `description`（目录列表只显示这一行），正文按需加载或常驻。常驻总量与单片上限都是 prompt 预算决定，写在 `agent/compaction.py` 的 `ContextBudget` 与 `prompt` 的 `SKILL_ALWAYS_*` 常量。
