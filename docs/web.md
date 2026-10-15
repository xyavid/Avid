# Web 传输层与前端

两层：`avid/web/` 是**内核侧的传输适配**（FastAPI 路由 + pydantic DTO + SSE 编帧 + 静态资源），`web/` 是**浏览器侧全部代码**。线格式（消息、附件、事件）的唯一所有者在内核侧，前端契约种子在 `web/src/api/types.ts` 与 `web/src/events/types.ts`，两侧漂移由 `tests/test_wire_contract.py` 与 `tests/test_event_contract.py` 拦。

## 内核侧端点面

| 入口 | 位置 |
|---|---|
| 应用 | `avid/web/app.py`（FastAPI），DTO 在 `schemas.py` |
| 路由 | `avid/web/routes/`：`sessions`、`runs`、`inputs`、`settings`、`search` 等 |
| SSE | 事件从 `agent/events.py` 的名单出发，经 `services/runs.py` 的重放缓冲编帧推给浏览器 |
| 静态资源 | `avid/web/static/`，构建产物由 `pnpm -C web run copy:dist` 交付进来 |

## 前端单点声明

| 改什么 | 唯一落点 |
|---|---|
| 加一个界面 | `web/src/surfaces/` 加页面并在 `web/src/app/App.tsx` 挂路由；颜色 / 字号 / 圆角只取 `web/src/styles/tokens.css` 的 token，不写散档 |
| 加一个右列面板 | `components/dock/Dock.tsx` 的 `PANELS` 加一条 + `state/dock.ts` 的 `DockPanelId` 与 `PANEL_IDS` 同步；面板视图在 `components/dock/`，数据由页面下发（Dock 是纯展示，不发请求） |
| 改对话时间线（段落顺序 / 工具行 / 思考段 / 收尾折叠） | `web/src/state/timeline.ts` 是单点（段落模型 + 事件→段落 + 历史归并 + 一键折叠判定 `turnGroups`），渲染在 `components/chat/Timeline.tsx`——过程与收尾共用它，别在页面里另起一套 |
| 改模型选择 / 候选来源 | `web/src/state/runModel.ts`（选哪个、记不记得住、候选没了怎么办）+ `components/chat/ModelButton.tsx`（画它）；推理强度另有 `components/chat/EffortButton.tsx`，档位同样来自候选自己的声明；候选来自 `GET /api/meta` 的 `capabilities.models`，设置页只负责那份列表 |
| 改输入区工具栏的排列 / 宽度适应 | `components/chat/Composer.tsx` 的 `data-testid="composer-toolbar"` 一行分三区（附件+权限 / 容量环+模型 / 推理强度+动作），`useCompact` 按行宽（阈值 `COMPACT_BELOW`）决定胶囊收不收文字 |
| 改 markdown 支持的写法 / 危险协议名单 | `web/src/markdown/parse.ts` 是「标准解析器（@lezer/markdown，CommonMark+GFM）→ 渲染模型」的翻译层与协议闸门，`Markdown.tsx` 只负责画（全程 React 元素，不注入 HTML）；软换行成断行、外链图降级成链接是有意偏离，写在模块注释里 |
| 改图片附件的同口径数字 | `web/src/state/imagePrep.ts`（内核侧单点是 `avid/attachments.py`） |
| 改「这次运行在哪棵树上」的取数与显示 | 取数只有一处：`GET /api/sessions/{id}/worktree`（会话那一行与右列「工作树」面板共用同一份读数，`api/client.getSessionWorktree`）；订阅与阶段状态单点在 `web/src/state/useRunStream.ts`——一个页面同一时刻只有一条 run 订阅，换会话即换订阅，非当前 run 的事件按 `run_id` 丢弃 |
