# ui/patterns — 会话时间线的渲染库

这一层的职责：把 `events/reducer` 的收敛结果（`TimelineEntry` / `ToolRun` / `ApprovalRequest`）
翻译成可读的玻璃界面。**只做渲染**——不取数、不发请求、不做分组判断；
分组与折叠策略由页面决定，这里只提供零件与默认值。

依赖方向：`patterns → lib/{ansi,diff,markdown,i18n}` 与 `patterns → ui/primitives`
（**不依赖 `ui/glass/`**：玻璃原语只被 L2/L3 用到）。反过来没有依赖，所以这一层可以
脱离 store 单测——`check:layers` 的规则 5 守着这条边。

---

## ToolCallCard

| 项 | 约定 |
| --- | --- |
| **状态** | 徽标文案取 `tools.status.*`，`role="alert"` 只加在 failed/denied 上。`running` 用 info 色 + `pulse`；`ok` 用 ok 色；`truncated` 用 warn 色，并在 bash 输出的最后一行追加 `── 已截断 ──`；`denied` 额外显示一行 `tools.deniedReason`（`role="alert"`）。内容为空（任何状态）统一显示 `tools.empty`，不渲染空框。 |
| **类型→读法** | `bash` 等宽 + SGR 上色（`lib/ansi`）；`read_file` 内容像 diff 时按 diff 渲染，否则全文；`write_file`/`edit_file` 用 `lib/diff` 生成红绿笔（add `bg-ok-bg/30 text-ok`、del `bg-danger-bg/30 text-danger`），工具输出本身就是 diff 时直接解析；`glob` 一行一个路径；`todo_write` 读 `arguments.todos` 画 `[x]/[~]/[ ]`；六个任务工具（create/update/claim/complete/get_task/can_start）渲染字段卡（id/subject/status/owner/blocked_by）；`subagent` 按 `=== i/n · description ===` 分段，段首加切分墨线；`load_skill` 显示技能名 + 字符数。 |
| **密度** | `comfy`：卡片 `p-4`，输出区 `max-h-96`。`compact`：卡片 `p-2`，输出区 `max-h-48`，适合时间线里大量连续调用。密度只影响内边距与最大高度，不影响信息取舍。 |
| **折叠默认值** | `defaultExpanded ?? false`——默认折叠。折叠态是一个 `w-fit max-w-[250px]` 的 `variant="secondary"` 按钮（玻璃面 + `--r-chip` 圆角），内容是 `tools.call` 小标 + 工具名 + 状态徽标；展开态是 `w-full` 的 `surface-card`。展开由内部 state 管理，调用方不控制（需要「记住上次展开」时由页面重建 key）。 |
| **a11y** | 折叠/展开都由 `Button` 承担，accessible name 来自按钮内部文字（`CALL bash 成功` / `收起`），因此不需要额外 `aria-label`；`aria-expanded` 表示当前展开态。错误态的状态徽标带 `role="alert"`，只在展开分支出现（折叠时不打断读屏）。diff 输出区用 `ol` + `aria-label={t('tools.inspector.diff')}`。正文、代码、工具输出**不旋转**——新语言不旋转任何元素（倾斜机制已随旧语言删除，`check:style` 的 rotate 禁令保留，守的是「别再引入」）。 |

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
| **状态** | 四种 `kind`：`user` 是靠右的 `surface-card`、`assistant` 是靠左的同族卡（半透明玻璃面 + 1px 高光边 + `--lift-2` 投影；方向相反、角色标记不同），`assistant` 正文走 `Markdown`；两者的卡片头是一枚 lucide 图标（`Bot` / `User`）+ 角色名；`tool` 是 `term` 等宽块 + `chat.message.role.tool` 徽标；`notice` 是 `surface-chip`（文案取 `chat.notice.compaction`）。unknown kind 一律按 assistant 处理，保证新增 kind 不会渲染成空白。**这里拿到的 assistant 条目一定有正文**：只声明工具调用、正文为空的回合由 `conversation/lib/groupTimeline` 直接跳过（否则每轮工具调用都多出一个只有角色名的空框），注入的提醒同理不进时间线。 |
| **密度** | `compact` → 内边距 `p-2`，`comfy` → `p-3`；只作用于用户卡与 assistant 正文容器。 |
| **折叠默认值** | 无折叠。notice 文案用 `truncate` + `max-w-[32rem]` 收敛长度，完整内容留给 inspector（`onInspect`）。 |
| **角色标记** | 两枚 lucide 图标：模型 `Bot`、用户 `User`。同一套图标契约（`strokeWidth 1.75` + `color: currentColor` + `aria-hidden`），**必须不同形**——标记的职责是区分作者，两卡共用一个形状等于没标。`e2e/messages.spec.ts` 断言两枚的 path 数据不相等（防「顺手统一」），另一条断言图标 `aria-hidden`、角色名由旁边的文字承担。**卡片不再轮换形状**：旧语言那三种手绘圆角随机制删除，同一份 spec 反过来断言「所有消息卡片同形」。 |
| **a11y** | `aria-live="polite"` **只加在 durable（`!entry.optimistic`）的 assistant 条目上**：乐观 delta 每帧都在变，播报等于噪音。动作行（复制文本 / 从此处分支）**常驻可见**，不做悬停显形——显隐只是额外一层谜（「有这功能」得先被猜到），而方框与高度档本来就一直在。动作按钮的 accessible name 来自内部文字。「从此处分支」只在条目有 `entryId` 时出现：乐观的 delta 条目还不是分叉点。条目级的「查看原始 JSON」已删除，检查器由工具卡的「查看」打开。 |
| **交互反馈** | 动作按钮用 `variant="secondary"`：**方框是本身就有的**（玻璃面 + `--glass-edge` 高光边 + `--r-chip` 圆角 + `--lift-1` 投影），与「改名」等次级按钮同族；悬停抬升一档投影并加一点亮度、按住收掉投影（**不位移**：高度由投影承担，`transform` 不参与表达层级），统一在 `ui/glass.css` 的 `.press` 里定义。`opacity` 不是这些按钮的设计变量。取值全部来自 `ui/tokens.css`，不要在调用点硬写边框、圆角或阴影。 |

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
  内建调色板类、**Tailwind 内建档位（`rounded-xl` / `shadow-lg` 这类）**、内联
  `borderRadius`/`fontFamily`、`z-[…]`、`transition-all`。
- **模糊只能来自 `ui/glass.css` 的 `--glass-blur`，且只允许落在四个面板类上**（列表内的
  `.surface-card` / `.surface-chip` 不模糊）：组件里写 `backdrop-blur-*` 或内联
  `backdropFilter` 即失败。两处细节与来历见设计文档 §8.9 / §14 的 C14、C15、C18。
- 没有裸 `<button>` / `<input>` / `<select>` / `<textarea>`：交互一律走 `ui/primitives`。
- 全部文案走 `t('ns.key')`；本目录用到的 key 见仓库根交付说明（新增 key 需并入
  `lib/i18n/dictionary.ts`，本目录不直接改字典）。
- 单文件 ≤200 行、单组件 ≤50 行：超长的工具读法被压成"工具 → 行"的纯函数，
  由 `ToolCallCard` 里唯一的 `Lines` 渲染器输出。
