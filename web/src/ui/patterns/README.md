# ui/patterns — 会话时间线的渲染库

这一层的职责：把 `events/reducer` 的收敛结果（`TimelineEntry` / `ToolRun` / `ApprovalRequest` /
`CompactionNote`）翻译成可读的涂鸦界面。**只做渲染**——不取数、不发请求、不做分组判断；
分组与折叠策略由页面决定，这里只提供零件与默认值。

依赖方向：`patterns → lib/{ansi,diff,markdown,i18n}` 与 `patterns → ui/primitives`。
反过来没有依赖，所以这一层可以脱离 store 单测。

---

## ToolCallCard

| 项 | 约定 |
| --- | --- |
| **状态** | 徽标文案取 `tools.status.*`，`role="alert"` 只加在 failed/denied 上。`running` 用 info 色 + `pulse`；`ok` 用 ok 色；`truncated` 用 warn 色，并在 bash 输出的最后一行追加 `── 已截断 ──`；`denied` 额外显示一行 `tools.deniedReason`（`role="alert"`）。内容为空（任何状态）统一显示 `tools.empty`，不渲染空框。 |
| **类型→读法** | `bash` 等宽 + SGR 上色（`lib/ansi`）；`read_file` 内容像 diff 时按 diff 渲染，否则全文；`write_file`/`edit_file` 用 `lib/diff` 生成红绿笔（add `bg-ok-bg/30 text-ok`、del `bg-danger-bg/30 text-danger`），工具输出本身就是 diff 时直接解析；`glob` 一行一个路径；`todo_write` 读 `arguments.todos` 画 `[x]/[~]/[ ]`；六个任务工具（create/update/claim/complete/get_task/can_start）渲染字段卡（id/subject/status/owner/blocked_by）；`subagent` 按 `=== i/n · description ===` 分段，段首加切分墨线；`load_skill` 显示技能名 + 字符数。 |
| **密度** | `comfy`：卡片 `p-4`，输出区 `max-h-96`。`compact`：卡片 `p-2`，输出区 `max-h-48`，适合时间线里大量连续调用。密度只影响内边距与最大高度，不影响信息取舍。 |
| **折叠默认值** | `defaultExpanded ?? false`——默认折叠。折叠态是 `w-fit max-w-[250px] px-4 py-2 border-hair` 的 chip，内容为 `tools.call` + 工具名 + 状态徽标；展开态是 `w-full border-bold` 卡片。展开由内部 state 管理，调用方不控制（需要"记住上次展开"时由页面重建 key）。 |
| **a11y** | 折叠/展开都由 `Button` 承担，accessible name 来自按钮内部文字（`CALL bash 成功` / `收起`），因此不需要额外 `aria-label`；`aria-expanded` 表示当前展开态。错误态的状态徽标带 `role="alert"`，只在展开分支出现（折叠时不打断读屏）。diff 输出区用 `ol` + `aria-label={t('tools.inspector.diff')}`。正文、代码、工具输出一律 **不 rotate**。 |

## StepGroup

| 项 | 约定 |
| --- | --- |
| **状态** | 组内先按 `status` 过滤掉 `failed`/`denied`（这两类必须单独可见，不能被折叠吃掉），因此组的状态只有"可见条数"。过滤后为 0 时组件返回 `null`——渲染一个空组是噪音。 |
| **密度** | 透传给组内每个 `ToolCallCard`；组自身的头部与折叠 chip 不随密度变化。 |
| **折叠默认值** | 可见条数 `< EVENT_GROUP_MIN_SIZE(2)` 时默认展开，否则默认折叠。折叠态显示 `tools.group.title`（`连续 {count} 次工具调用`）并带调用方给的 `title` 前缀。 |
| **a11y** | 折叠 chip 与展开态头部的收起按钮都是 `Button`，accessible name 来自内部文字；`aria-expanded` 表示展开态。组内卡片沿用各自的可访问名。 |

## EntryRow

| 项 | 约定 |
| --- | --- |
| **状态** | 四种 `kind` 四种形状：`user` 是右侧便签（`ml-auto max-w-[80%]` + `sketch-card`）；`assistant` 走 `Markdown`；`tool` 是 `term` 等宽块 + `chat.message.role.tool` 徽标；`notice` 是 `sketch-chip`（文案取 `chat.notice.{todo,nudge,compaction}`）。unknown kind 一律按 assistant 处理，保证新增 kind 不会渲染成空白。 |
| **密度** | `compact` → 内边距 `p-2`，`comfy` → `p-3`；只作用于用户便签与 assistant 正文容器。 |
| **折叠默认值** | 无折叠。notice 文案用 `truncate` + `max-w-[32rem]` 收敛长度，完整内容留给 inspector（`onInspect`）。 |
| **a11y** | `aria-live="polite"` **只加在 durable（`!entry.optimistic`）的 assistant 条目上**：乐观 delta 每帧都在变，播报等于噪音。动作行（复制 / 查看原始 JSON）默认 `opacity-0`，`group-hover:opacity-100`；`focus-visible:opacity-100` 写在**按钮自己**身上，键盘 tab 到哪个哪个显形（若把透明放在容器上，子元素会被一起变透明，键盘用户永远看不见动作）。两个动作按钮的 accessible name 来自内部文字（`复制文本` / `查看原始 JSON`）。 |

## CompactionNotice

| 项 | 约定 |
| --- | --- |
| **状态** | 无状态机：压缩是既成事实。显示 `chat.notice.compaction` 标签、`step — detail`、以及 `before → after` 字符数（`tools.chars`）。 |
| **密度** | 不随密度变化——它是一条窄 chip，放在时间线与检查器里都不该变形。 |
| **折叠默认值** | 不折叠。压缩事件一天也没几次，藏起来只会让人怀疑上下文去哪了。 |
| **a11y** | 纯静态文本（读屏器按文档顺序读）。外层不设 `aria-live`：它在时间线里由 `EntryRow` 统一播报，重复播报是 bug。 |

## ApprovalBar

| 项 | 约定 |
| --- | --- |
| **状态** | 未决：warn 徽标 + 参数 + 相对有效期（`approvals.expires`，`Intl.RelativeTimeFormat` 按当前 locale 格式化）。已决（`decision !== null`）：ok/danger 徽标 + `approvals.answered.{allow,deny}` + `resolvedReason`，两个按钮禁用。过期（`expiresAt < Date.now()` 且未决）：`role="alert"` 显示 `approvals.expired`，两个按钮禁用（未答复一律收敛为拒绝）。`busy` 时按钮禁用但不改结论。 |
| **密度** | 不随密度变化：审批是打断式交互，始终用完整卡片与等宽参数。参数区 `max-h-48` 可滚动。 |
| **折叠默认值** | 默认展开（`open = true`）。审批必须让人看见要批准什么，收起只是给已经看懂的人一个整理视图的按钮。 |
| **a11y** | 拒绝按钮 `autoFocus`，accessible name 来自内部文字（`拒绝` / `允许一次`）。键盘：**Enter = 允许、Escape = 拒绝**，处理函数挂在卡片上并对 Enter 调 `preventDefault`——否则焦点在拒绝按钮上按 Enter 会先触发按钮自身的 click，同一个键就有了两种含义（鼠标/空格仍然按按钮语义走）。参数区 `aria-label={t('approvals.toolArguments')}`。过期提示与失败态用 `role="alert"`。同一个 `approvalId` 的重复提交由服务端幂等兜底，前端只负责 `busy` 期间禁用。 |

---

## 与门禁的关系

- 视觉值全部来自 token（构件类 + 允许的 Tailwind 类），没有 hex/rgb、`shadow-[…]`、`dark:`、
  内建调色板类、内联 `borderRadius`/`fontFamily`、`z-[…]`、`transition-all`。
- 没有裸 `<button>` / `<input>` / `<select>` / `<textarea>`：交互一律走 `ui/primitives`。
- 全部文案走 `t('ns.key')`；本目录用到的 key 见仓库根交付说明（新增 key 需并入
  `lib/i18n/dictionary.ts`，本目录不直接改字典）。
- 单文件 ≤200 行、单组件 ≤50 行：超长的工具读法被压成"工具 → 行"的纯函数，
  由 `ToolCallCard` 里唯一的 `Lines` 渲染器输出。
