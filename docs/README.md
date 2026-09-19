# 文档目录

本目录只放**正式文档**：描述项目当前状态、面向使用者与协作者的稳定内容。

## 收录标准

| 进 `docs/`（入库） | 留 `dev/`（本地，不入库） |
|---|---|
| 项目说明、使用手册 | 开发计划、阶段路线 |
| API 文档 | 需求草稿、方案草稿 |
| 部署与配置说明 | 进度跟踪、验收记录 |
| 冻结后的设计与规格 | 会议记录、临时笔记、阶段出图 |

判断标准：**换个时间点重新拉取仓库，这份文档是否仍然成立？** 会随开发进程作废的，属于过程文档。

## 目录与命名

```
docs/
├── README.md              本文件：收录标准
├── guide/                 使用手册      docs/guide/getting-started.md、web-ui.md
├── design/                设计与规格    docs/design/runtime-architecture.md、workspace-permission.md…
└── status/                项目现状五份  CURRENT_STATE.md、CAPABILITIES.md、BENCHMARK.md、ARCHITECTURE.md、ROADMAP.md
```

- `guide/`、`design/` 命名全小写 `kebab-case.md`，**不加日期前缀**——正式文档描述当前状态，不按时间归档。
- `status/` 是**例外**：五份文件名用大写 `SCREAMING_SNAKE.md`，与 `design/` 下的规格文档一眼可分；`docs/status/` 内新增文件仍需改 `.gitignore` 白名单。
- 一级目录固定为五类：`guide/`、`api/`、`deploy/`、`design/`、`status/`。**`api/`、`deploy/`
  已白名单放行但还没有文档**（工具协议与配置说明目前住在 `guide/` 里）。新增类别必须同时改
  `.gitignore` 白名单，否则文件会被静默忽略。

### status/ 五份文档的分工与更新时机

| 文件 | 唯一职责 | 什么时候必须改 |
|---|---|---|
| `CURRENT_STATE.md` | 只回答九个问题（是什么 / 解决什么 / 能 / 不能 / 最可靠 / 最差 / 瓶颈 / 未知 / 下一版） | 每次阶段收尾，或任一问的答案被实测推翻时 |
| `CAPABILITIES.md` | 当前**已具备**能力的清单与入口（工具、权限、会话、任务、Web、CLI） | 能力增删、参数或语义变更时 |
| `BENCHMARK.md` | 性能与效果基准：**有什么数字、缺什么数字、怎么补** | 基准首次建立、阈值冻结、或跑出一轮基线时 |
| `ARCHITECTURE.md` | 整体架构与模块关系的**总览**（细节留在 `design/`） | 分层、依赖方向、所有权或门禁变更时 |
| `ROADMAP.md` | **未来**演进方向与解禁条件（已完成阶段账本在 `dev/plan/roadmap.md`，不入库） | 阶段性目标确定或被否掉时 |

写这五份时的硬要求：**每条结论要么能在代码里指出出处，要么显式标为未验证假设**；不许把
`dev/` 下过程文档的结论当成事实照抄，也不许在 `status/` 里重复 `design/` 已有的论证。


## 过程文档放哪

`dev/` 与 `docs/` 平级，整体被 `.gitignore` 忽略，**只存在于本地**：

```
dev/
├── plan/            开发计划、阶段路线、进度跟踪
├── drafts/          需求与方案草稿
├── architecture/    阶段出图（SVG）
├── notes/           临时笔记
└── meetings/        会议记录
```

- 命名 `YYYY-MM-DD-<kebab-case>.md`，带日期便于按时间排序——过程文档天然按时间归档。
- 这些文件不在任何远端。需要异地留存时自行同步到私有位置（例如 `~/Documents/avid-dev/`）。

## 从版本库移除已入库的过程文档

`git rm --cached` 只动索引，工作区文件原样保留：

```bash
git rm --cached <path>    # 移除跟踪，本地副本不动
```

`.gitignore` 对**已跟踪**文件无效，必须先执行这一步。
