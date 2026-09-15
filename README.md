# Avid

自建的 agent 运行时（harness）：模型调用、工具执行、多步循环、上下文与记忆、权限、评测各层都掌握在自己手里，做到可替换、可调试、可度量。

**当前进度**：阶段 0 —— 工程基线与最小模型调用。还没有工具调用，agent 循环从阶段 1 开始。

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

```bash
uv run --env-file .env avid "用一句话说明你是谁"
```

stdout 打印模型回复，stderr 打印本轮 token 用量。

## 开发

```bash
uv run pytest    # 全部测试，不联网
```
