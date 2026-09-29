# 安全分层 E2E（阶段 26）

一条命令，产出可重复检查的结论：`artifact.json`。

```console
uv run --no-sync python benchmarks/sandbox_boundary/run.py
# 自检：只要有一条结论不成立就打印 [FAIL] 并以非 0 退出
```

## 它验证什么

**策略 / 审批 / 沙箱 / 网络 / 审计各自拦住了什么**。四个臂跑同一批攻击性探针：

| 臂 | approval | sandbox | 它证明什么 |
|---|---|---|---|
| `manual` | user（一律**拒**） | workspace | 策略层能挡住什么 |
| `manual_yes` | user（一律**准**） | workspace | **即使人全批准，还剩下什么挡得住** |
| `auto` | classifier | workspace | 不问你也能挡住；且一次都不问你 |
| `full` | none | disabled | 显式关掉边界之后，哪些东西**照样**挡、哪些真的落到宿主上 |

`manual_yes` 是这份产物的重点：它把"审批"这条边界彻底让开，于是剩下拦住攻击的只可能
是沙箱——这就是"Sandbox 是最后一道、最不该相信模型的边界"的可执行证据。

## 探针与证据的类型

| 类型 | 例子 | 证据长什么样 |
|---|---|---|
| 策略拒绝 | `rm -rf /`、读 `~/.ssh`、写 `.git/hooks` | 没有 `tool_call_started` 的完成、有一条 `tool_call_denied`（带 `kind`） |
| 越过沙箱 | 写 `/var/tmp/...`（工作区之外） | `manual` 问（无人答复→拒）；`manual_yes` 批准后**真的写进去**（能力授予是实的）；`auto` 拒 |
| 沙箱已有能力 | 读 `/etc/hostname`、写沙箱私有的 `/tmp` | 四个臂都直接执行、不打问号——沙箱用 `--ro-bind / /` 保证读，用私有 tmpfs 保证 `/tmp` |
| 物理边界 | 网络、宿主 `/tmp`、掩蔽的宿主凭据、只读挂载 | 未批准的解释器调用先拒绝；`manual_yes` 批准后再以 `Network is unreachable` / `Read-only file system` / `RC_MASKED` 验证 OS 强制边界 |
| 审计 | 每条裁决 | `~/.avid/audit/*.jsonl` 里每个臂 ≥ 探针条数的 `decision` 记录，带三轴快照 |

**边界是沙箱能力，不是工作区**（`sandbox_boundary_is_not_a_workspace_boundary`）：
`outside_read_benign` 与 `tmp_write_is_ephemeral` 证明"在工作区之外"本身不是拦的理由
（沙箱已经把"读整个文件系统""写私有 `/tmp`"变成已有能力），
`approved_capability_is_real` 证明要审批的是"写沙箱保证不了的目标"、且批准后能力是真的。
这与 Codex `workspace-write`（reads files anywhere、edits only `cwd` + `writable_roots`）、
Claude Code 沙箱的 read / write 分层、opencode `read` 默认 allow 是同一口径。

**看不见的越界写**（`invisible_outside_write`）证明两层各有职责：解释器执行
在 `manual` 下要批准、`auto` 下拒绝；`manual_yes` 批准后，命令里的路径由解释器用
`chr(47)` 拼出，broker 仍看不到目标，但只读挂载物理拦住实际写入。

## 真实的部分与替换掉的部分

真的：`svc` 运行注册表与会话落盘、内核循环、Tool Broker、Policy Engine、四级 deny 阶梯、
`SandboxSpec` 组装、`bwrap` 真执行、审批表、事件流、审计落盘。

替换掉的只有一个：**模型**换成脚本（每个探针一轮，真模型不会每次都发同一批）。

## 单元测试与这里的分工

决策表、argv 组装、环境白名单、降级语义在 `tests/test_policy_engine.py` 与
`tests/test_sandbox.py` 里逐条断言（跑得快、进 CI）；`tests/test_sandbox.py` 末尾还有 5 条
**真跑**的用例。这里补的是端到端证据：真服务 + 真会话 + 真沙箱 + 真审批 + 真审计，并把
结论落成仓库里的产物。

## 参数

```console
uv run --no-sync python benchmarks/sandbox_boundary/run.py --arms manual_yes,full --out /tmp/a.json
```

## 这台机器上的前置

需要 `bwrap`（bubblewrap）。没有它时 `manual` / `auto` 的沙箱态会降级（见
`SandboxSpec.degraded`），产物里那几条物理边界结论会失败——这正是"不静默降级"该有的样子。
