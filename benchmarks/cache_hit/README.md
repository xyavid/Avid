# 前缀缓存命中率（cache_hit）

**它回答的问题**：`ContextManager` 首轮冻结 system prompt 这件事，到底换来了多少
provider 侧的前缀缓存命中——"冻结"是手段，命中省下的 token 才是收益。

**它不回答的问题**：冻结是否正确（那是设计判据与单元测试的事）、其它内容该不该进
system（那是 `policy/prompt.py` 的取舍）。

## 两个臂

| 臂 | 模型 | 回答什么 |
|---|---|---|
| `frozen_prefix` | 离线标尺（不联网、不计费） | 一次运行内发给模型的 system 逐字节稳定、tail 每轮变化——缓存能命中的**前提** |
| `live_cache` | 真模型（`--live`，读 `.env`） | 第 2 轮起 provider 上报 `cache_read_tokens>0`，命中率中位数 ≥ 阈值——命中**真的发生** |

## 怎么跑

```bash
# 离线臂：不需要模型配置，也不产生任何 API 调用
uv run --no-sync python benchmarks/cache_hit/run.py

# 真模型臂：4 轮左右的微小对话，成本可忽略，但要走真实网关
uv run --env-file .env python benchmarks/cache_hit/run.py --live
uv run --env-file .env python benchmarks/cache_hit/run.py --live --min-ratio 0.8
```

产物：`artifact.json`（每轮 system 哈希、prompt/cache_read/cache_write token 与
比率、结论）。结论是布尔量，重跑同一条命令得到同样的结论；退出码 1 表示有结论
不成立。

## 结论清单

- **C1**（离线）：同一运行 N 次调用的 system 哈希只有 1 种，tail 哈希有 N 种。
- **C2**（live）：provider 在 usage 里上报缓存命中，第 2 轮起每轮 `cache_read_tokens>0`。
- **C3**（live）：第 2 轮起命中率（cache_read/prompt）的中位数 ≥ `--min-ratio`（默认 0.5）。

## live 臂的两个前提

1. **网关要上报缓存 token**：`ai/usage.py` 已归一 OpenAI 系（`cached_tokens` /
   `prompt_cache_hit_tokens`）等写法；不上报的网关会把 C2/C3 判为不成立并在产物里
   写明"未上报"——这是环境事实，不是代码缺陷。
2. **对话要真的走多轮**：live 臂的问题要求模型逐个读完三个文件再汇总；若模型
   一轮就答完，C2/C3 因"没有后续轮"不成立。

## 与其它仪器的关系

- 压缩阈值是否随窗口派生：`benchmarks/context_window/`。
- 任务成功率与 token 账：`benchmarks/avidbench/`（`python -m benchmarks.run`）。
- 本仪器只管"稳定前缀 → 缓存命中"这一段链路。
