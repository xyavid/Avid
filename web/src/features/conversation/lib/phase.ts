/**
 * 「运行是否还没收敛」的唯一判定。
 *
 * 原先这个四阶段集合在**两个文件里各写了一份字面量**：`features/conversation` 的
 * `ConversationView`（决定要不要画处理中卡片）与 `routes/ConversationRoute`（决定输入条
 * 与分支切换要不要禁用）。两份之间没有共同的守护者，加一个阶段时只改一处，界面就会
 * 出现「按钮可点但运行还在忙」这类静默不一致。
 *
 * 为什么落在这里（L2 的 feature 内），而不是 `events/reducer`（`RunPhase` 的定义处）或
 * `lib/`：
 *   · `RunPhase` 的定义处在承重层，本轮一个字节都不动（阶段 23a 判据 Ac6）；
 *   · `lib/` 是承重层的纯函数区，同样不动；
 *   · 这是**展示语义**（哪些阶段算是「还在跑」→ 要不要禁用交互），消费者是 L2 的视图与
 *     L4 的 route，所以归属 L2。route 通过 feature 的公共出口拿它，方向合法（§3.4）。
 */
import type { RunPhase } from '../../../events/reducer'

/**
 * 运行尚未收敛的阶段。这四个之外都是终态或还没开始：
 * `done` / `failed` / `cancelled` 是终态，`idle` 是本会话还没有跑过。
 */
const ACTIVE_PHASES: ReadonlySet<RunPhase> = new Set<RunPhase>([
  'submitting',
  'streaming',
  'awaiting_approval',
  'cancelling',
])

/** 输入条与分支切换据此禁用：运行没收敛时给出的入口只会失败。 */
export function isActivePhase(phase: RunPhase): boolean {
  return ACTIVE_PHASES.has(phase)
}
