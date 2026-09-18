# Avid

自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限、评测各层都掌握在自己手里，做到可替换、可调试、可度量。

**当前进度**：内核（模型调用、循环、工具、会话持久化、任务图、事件层）与
**本地 Web 界面**（会话时间线、审批队列、任务板、技能目录、设置）都已跑通。

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

开发期前端热更新：另起 `pnpm -C web install && pnpm -C web dev`（Vite 代理 `/api`）。
交付形态是「分离开发、单进程交付」：`pnpm -C web build && pnpm -C web run copy:dist`
把产物复制进 `src/avid/web/static/`，随 wheel 分发，安装者不需要 Node。

页面与接口的对应关系、事件分档规则与验收命令见 `docs/guide/web-ui.md`。

## 开发

```bash
uv run pytest              # 内核与 API 全部测试，不联网
pnpm -C web run verify     # 前端门禁：层禁令、token、i18n、体积、单测
AVID_E2E=1 pnpm -C web test:e2e   # 浏览器冒烟（需先 playwright install chromium）
```
