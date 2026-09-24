# 并行工具调用 E2E（阶段 25）

一条命令，产出可重复检查的结论：`artifact.json`。

```console
uv run --no-sync python benchmarks/parallel_tools/run.py
# 自检：只要有一个结论不成立，脚本以非 0 退出，并打印 [FAIL] 那一行
```

## 它验证什么

模型一次回复里请求 5 个工具时，Avid 应当**同时**发起其中可并发的部分，而不是逐个跑：

| 批内调用 | 类别 | 执行方式 |
|---|---|---|
| `read_file a.txt` / `read_file b.txt` / `glob *.txt` / `read_file c.txt` | 并发安全 | 同一段，一起跑（上限由 `AVID_MAX_PARALLEL_TOOL_CALLS` 决定） |
| `write_file out.txt` | 独占 | 屏障，单独跑，与两侧的读**不重叠** |

三个臂各跑一遍同一批：上限 `10` / `2` / `1`（1 = 改动前的完全串行）。

## `checks` 的每一条是什么意思

| 结论 | 依据 |
|---|---|
| transcript 逐条相同（含顺序） | 两个臂的 tool 消息 `(tool_call_id, content)` 序列逐条相等——并发只改**完成顺序**，不改写进会话的顺序与内容 |
| 最终回答相同 | 两个臂的 `record.text` 相等 |
| 并行档确实重叠 | `tool_call_started/finished` 的 `ts` 区间有交集 |
| 串行档一次都没有重叠 | 上限=1 时零个重叠对，峰值并发恒为 1 |
| 写调用是屏障 | `c5` 的时间窗与任何读的时间窗都不相交 |
| 上限被遵守 | 上限=2 时峰值并发 = 2（4 个读被拆成两批） |
| `parallel` 字段可信 | `tool_call_started` 事件带 `parallel=4`（并发段宽度）或 `1`（独占） |

`wall_clock_ms` 只是参考值（机器快慢会变）；**结论是布尔量，不随机器变化**。

## 真实的部分与替换掉的部分

真的：`svc` 运行注册表与会话落盘、内核循环、`execution.execute_batch`、工具注册表
（`avid.tools` 里那一个 handler）、参数校验、权限、输出截断、事件流。

替换掉的只有两样，都在脚本顶部写明：

* **模型** → 脚本（真模型不会每次都发同一批 5 个调用）；
* **每个工具调用的延迟** → 注入固定 150 ms。`read_file` 读几十字节是亚毫秒级的，
  不注入延迟就看不出"有没有重叠"。注入的是延迟，不是行为。

## 单元测试与这里的分工

调度规则（分段、屏障、失败隔离、取消、与串行逐字节等价）在 `tests/test_execution.py`
里用 `threading.Barrier` 断言，跑得快、进 CI；这里补的是**端到端**证据：真服务、真会话、
真事件流，并且把结论落成仓库里的产物。风格与 `avidbench` 一致：脚本 + 可打开的 JSON。

## 参数

```console
uv run --no-sync python benchmarks/parallel_tools/run.py --latency-ms 300 --arms 8,1 --out /tmp/a.json
```
