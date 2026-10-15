# docs 索引

装两样东西：**描述性现状**（只描述、不排期；每条结论锚到代码单点——路径 + 符号，不写行号）与**判据**（不含代码事实，因而无从腐烂，`design.md` 是唯一一份）。过期与脱锚由 `tests/test_docs_contract.py` 当场抓出来。

## 什么进 docs，什么进 dev

| | 装什么 |
|---|---|
| `docs/` | 「照着它能找到该改哪里、该怎么判」的东西：数据流、子系统、改动落点、命令清单、设计判据 |
| `dev/` | 只对一次开发有意义的：计划、诊断、架构图、会议记录、参考项目副本（不入库） |

现状问代码，不问文档：模块职责与不变量在 docstring，跨包边界在 `tests/test_web_boundaries.py`，前端契约在 `test_wire_contract.py` / `test_event_contract.py`。

## 五份

- [kernel.md](kernel.md) — 数据流、运行生命周期、上下文与压缩、跨包分层门禁（A1–A15）
- [modules.md](modules.md) — 子系统清单、入口点清单
- [extending.md](extending.md) — 改动落点全表
- [web.md](web.md) — 前端单点声明、端点面、SSE 与静态资源交付
- [design.md](design.md) — 架构推导判据（十二组检查点；唯一一份非现状）

环境、门禁命令与提交 / 注释 / 测试约定见根目录 [CONTRIBUTING.md](../CONTRIBUTING.md)；安装、配置项与 CLI 全量参数见 `README.md` 与 `README.zh-CN.md`。
