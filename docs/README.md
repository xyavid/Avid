# docs 索引

装**描述性现状**：只描述，不排期。两条规矩：每条结论锚到代码单点（路径 + 符号，不写行号）；过时的描述由 `tests/test_docs_contract.py` 当场抓出来。

## 什么进 docs，什么进 dev

| | 装什么 |
|---|---|
| `docs/` | 「照着它能找到该改哪里」的东西：数据流、子系统、改动落点、命令清单 |
| `dev/` | 只对一次开发有意义的：计划、诊断、架构图、会议记录、参考项目副本（不入库） |

现状问代码，不问文档：模块职责与不变量在 docstring，跨包边界在 `tests/test_web_boundaries.py`，前端契约在 `test_wire_contract.py` / `test_event_contract.py`。

## 五份

- [kernel.md](kernel.md) — 数据流、运行生命周期、上下文与压缩、跨包分层门禁（A1–A14）
- [modules.md](modules.md) — 子系统清单、入口点清单
- [extending.md](extending.md) — 改动落点全表
- [web.md](web.md) — 前端单点声明、端点面、SSE 与静态资源交付
- [commands.md](commands.md) — 常用命令、测试门禁、E2E 举证要求

更高层的规则与指引见根目录 [AGENTS.md](../AGENTS.md)（它同时是每个运行的 bootstrap 块，体积受门禁约束）；安装、配置项与 CLI 全量参数见 `README.md` 与 `README.zh-CN.md`。
