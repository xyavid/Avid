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
└── design/                设计与规格    docs/design/runtime-architecture.md、workspace-permission.md…
```

- 命名全小写 `kebab-case.md`，**不加日期前缀**——正式文档描述当前状态，不按时间归档。
- 一级目录固定为四类：`guide/`、`api/`、`deploy/`、`design/`。**当前只有前两类与第四类
  有内容**（`api/`、`deploy/` 已白名单放行但还没有文档——工具协议与配置说明目前住在
  `guide/` 里）。新增类别必须同时改 `.gitignore` 白名单，否则文件会被静默忽略。

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
