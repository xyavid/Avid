<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/src/assets/avid-logo-dark.svg">
    <img src="web/src/assets/avid-logo-light.svg" alt="Avid" width="294">
  </picture>
</p>

# Avid

> 可扩展的 agent 运行时：模型调用、工具执行、多步循环、上下文与记忆、权限各层都自己掌控，
> 可替换、可调试。CLI 与本地 Web 服务共用同一个内核。

中文 · [English](README.md)

## 功能

- **工具调用** —— 读写改文件、glob、shell、todo 等内置工具，加工作区声明的 MCP 工具，一律参数校验后执行。
- **子智能体** —— 互不依赖的子任务并行派发，每个子任务拿到一份结构化的任务提示。
- **技能** —— 一段可复用的操作说明（`SKILL.md`）：目录里只有描述，命中即载入全文。
- **上下文管理** —— 分区装配 + 压缩成结构化检查点，事实与假设分开记，长时间任务不掉线。
- **会话持久化** —— 对话与状态落盘，带分支与变更线；`/rewind` 可回滚对话指针与文件。
  所有工作区的会话集中在一个目录里（默认 `~/.avid/sessions`，可以搬到别的盘）。
- **内容检索** —— 本地 SQLite 索引（FTS5 + trigram）覆盖所有会话的正文：找得到「我在哪次对话里说过这句话」，
  点开就落到那次对话（消息命中会高亮；工具输出命中落在同一段对话里）。索引是派生数据——删掉就从 JSONL 重建。
- **模型配置** —— BYOK，四种协议（OpenAI 兼容 / Responses / Anthropic / Ollama）换着用，换模型不动内核。
- **权限** —— 默认直接执行；毁灭级命令先确认，宿主凭据拒读，可选沙箱与审计。

## 快速开始

```bash
git clone https://github.com/xyavid/Avid.git && cd Avid
uv sync --extra web                          # 内核 + Web 依赖（只跑 CLI 就别带 [web]）
uv run avid --agent "读 pyproject.toml，告诉我项目名"
```

```bash
uv run avid                    # 交互会话：/compact 压缩、/rewind 回滚、/<技能名> 载入技能
uv run avid web --port 8765    # 浏览器界面 → http://127.0.0.1:8765
uv run avid session search "聊过的某个词"    # 按内容检索历史会话
uv run avid index check --fix               # 校验检索索引；补/修/重建
```

`avid web` 之前先交付一次前端产物：

```bash
pnpm -C web install && pnpm -C web run copy:dist
```

想在任何目录直接敲 `avid`，把它装成命令：`uv tool install --editable ".[web]"`。
全部参数见 `avid --help`、`avid web --help`、`avid workspace --help`、`avid session --help`。

## 概念

- **Agent** —— 一次运行：模型调用 → 工具执行 → 结果回灌的多步循环，直到给出答复或终止。
- **Tools** —— 模型可调用的动作；声明、实现与参数校验写在一处，无需另注册。
- **Skills** —— 可复用的操作说明；命中时把全文读进上下文，或按配置常驻。
- **Subagents** —— 独立跑的子 agent：看不到主对话，只带一份任务提示，结果回到主 agent。
- **Sessions** —— 对话与状态的落盘单位，可续接、可分支、可回滚。

## 配置

- **工作区约定**：放一份 `AGENTS.md` 在工作区根目录，它会自动进入模型的常驻上下文。
- **模型连接（BYOK）**：`~/.avid/models.json`（提供商与绑定，只存密钥引用）+ `~/.avid/secrets.json`
  （明文密钥，0600）。用界面「设置 → 模型」维护，或手编这两份文件；没配就发送消息会提示缺什么。
- **确认与授权**：非交互场景用 `--yes` 代答毁灭级确认；`--allow-full-access` 跳过确认并关沙箱
  （凭据拒读仍然生效）。
- **会话存储**：所有工作区的会话集中在一个目录里，按工作区 id 分子目录（工作区的名字在注册表里，
  不在路径上）。默认 `~/.avid/sessions`；改位置用界面「设置 → 会话存储」或环境变量
  `AVID_SESSIONS_DIR`。改位置**不搬已有会话**——搬是 `avid session migrate` 的事（它先打印
  清单再动手；`avid session dir` 告诉你会话现在在哪，改过位置之后它还会顺着索引里记着的旧目录
  找一遍，不让旧会话搁浅）。**有运行在跑时改位置会被拒**：那次运行写的是老位置，得先让它跑完。
- **检索索引**：`~/.avid/index/sessions.sqlite`，从会话文件建起、随写随补（一轮的消息落库后立刻进索引）。
  它随时可以丢：`avid index check` 说清哪里落后/坏了，`avid index rebuild` 重建，删掉它只是下次重新建一遍。
  读会话不需要它。
- **环境变量**：可选，日常运行不需要。

| 变量 | 含义 |
|---|---|
| `AVID_HOME` | 整个用户级目录的落点（设置、注册表、会话、审计），默认 `~/.avid` |
| `AVID_SESSIONS_DIR` | 把会话目录放到别处（优先于设置文件里的值） |
| `AVID_INDEX_DIR` | 把派生索引放到别处 |
| `AVID_AUDIT_DIR` | 把审计 JSONL 单独放到一个目录 |
| `AVID_MAX_PARALLEL_TOOL_CALLS` | 每步并行工具调用数，默认 10 |
| `AVID_MODEL_INFO` | 设为 `off` 就不去问 provider 要模型窗口 |
| `AVID_SANDBOX_BIN` | 换一个 bwrap 可执行文件（诊断 / 打包） |
| `AVID_ALLOWED_HOSTS` | Web 服务额外信任的域名（局域网部署） |


## 开发

```bash
uv sync --extra web                          # 依赖
uv run pytest                                # 测试
uv run ruff check avid tests && uv run mypy   # 静态检查
pnpm -C web run verify                       # 前端：类型检查 + 测试 + 构建 + 体积门禁
```

协作约定（提交格式、测试与注释纪律）见 [AGENTS.md](AGENTS.md)。

## 许可

[MIT](LICENSE) © 2026 xyavid
