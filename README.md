# Avid

自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限、评测各层都掌握在自己手里，做到可替换、可调试、可度量。

**当前进度**：Agent 循环已跑通，并注册了首个工具 `read_file`（模型可自主读取工作区内的文件）。

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

`bash` / `write_file` / `edit_file` 在执行前会请求确认，提示写在 stderr。非交互场景加 `--yes` 跳过审批——**硬拒绝闸门仍然生效**，黑名单里的命令一律不执行。`read_file` / `glob` 是只读的，不弹确认。

stdout 打印模型回复，stderr 打印逐轮 trace 与 token 用量。

## 开发

```bash
uv run pytest    # 全部测试，不联网
```
