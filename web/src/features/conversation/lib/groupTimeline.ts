/**
 * 时间线分块：把 reducer 产出的**线性** TimelineEntry[] 折成「展示块」。
 *
 * 为什么是独立纯函数而不是在 Timeline 里顺手算：
 *   1. 合并规则是展示语义（相邻同类合并、notice 不并入），与 DOM 无关，
 *      放这里可以用最便宜的 node 用例把「条目守恒」这条不变量钉死；
 *   2. blocks 由调用方 memo 得住，流式追加时只有尾部块重新渲染。
 *
 * 四个变体的形状完全一致（key + entries 数组），组件层因此不必为 user/notice
 * 单独分叉"单条还是多条"两种处理；取工具调用 id 统一走 `blockToolCallId`。
 *
 * 类型从 reducer 引（`import type` 会被完全擦除，所以本模块在 reducer 落地前后都能跑用例）。
 */
import type { TimelineEntry } from '../../../events/reducer'

export type TimelineBlock =
  | { kind: 'user'; key: string; entries: TimelineEntry[] } // 相邻 user 合并（同一轮连发）
  | { kind: 'assistant'; key: string; entries: TimelineEntry[] } // 相邻 assistant 合并
  | { kind: 'tool'; key: string; entries: TimelineEntry[] } // 按相邻合并，不按 toolCallId 跨段合并
  | { kind: 'notice'; key: string; entries: TimelineEntry[] }

/**
 * 造一个新块。写成 switch 而不是 `{ kind: entry.kind, ... }`：
 * 联合类型的判别字段不能直接"透传"给另一个联合类型，TS 需要逐字面量分支才能收窄。
 */
function makeBlock(kind: TimelineEntry['kind'], entry: TimelineEntry): TimelineBlock {
  switch (kind) {
    case 'user':
      return { kind: 'user', key: `user:${entry.id}`, entries: [entry] }
    case 'assistant':
      return { kind: 'assistant', key: `assistant:${entry.id}`, entries: [entry] }
    case 'tool':
      return { kind: 'tool', key: `tool:${entry.id}`, entries: [entry] }
    case 'notice':
      return { kind: 'notice', key: `notice:${entry.id}`, entries: [entry] }
  }
}

/**
 * 从块里取出工具调用 id：tool 块取**首条**的 `toolCallId`；非 tool 块返回 null。
 *
 * 取首条而不是"任一条"：同一次调用的 started/finished 带的是同一个 id，
 * 首条一定是发起它的那条，最不容易被后到的补写事件覆盖。
 */
export function blockToolCallId(block: TimelineBlock): string | null {
  if (block.kind !== 'tool') return null
  const first = block.entries[0]
  return first?.toolCallId ?? null
}

/**
 * 这条能不能并进上一个块。
 *
 * tool 的门槛比别的类高一档：**相邻且 toolCallId 相同**。理由在渲染侧——
 * 一个 tool 块只渲染**一张** ToolCard，卡上的数据是按 `blockToolCallId(block)` 查出来的；
 * 把两次不同调用并进同一个块，第二次调用在界面上就消失了（条目还在块里，但没人读）。
 * 那正是描述里反对的"静默丢一块"。没有 toolCallId 的 tool 条目一律各自成块：
 * 无法证明它属于哪次调用时，合并等于编造归属。
 */
function canMergeWith(last: TimelineBlock | undefined, entry: TimelineEntry): boolean {
  if (last === undefined || last.kind !== entry.kind) return false
  // notice 是时间线上的"插播"，绝不并入相邻组：并进去会让人以为它是那条消息的一部分。
  if (entry.kind === 'notice') return false
  if (entry.kind === 'tool') {
    return entry.toolCallId !== undefined && entry.toolCallId === blockToolCallId(last)
  }
  return true
}

/**
 * 把线性条目折成展示块。规则：
 *   · 只合并**相邻同类**：user（同一轮连发）、assistant（流式增量与终态补写连续到达）、
 *     tool（同一次调用的 started/finished/denied 连续到达）；
 *   · tool **不按 toolCallId 跨段合并**：中间插了别的条目，说明时间线上确实发生了别的事，
 *     把两段接起来会伪造出并不存在的顺序；
 *   · 空输入返回空数组。
 *
 * **条目守恒不变量**：输入里的每一条都恰好落在一个块里，既不吞也不复制
 * （单测用"输入输出条目集合相等"钉死）。
 */
export function groupTimeline(entries: readonly TimelineEntry[]): TimelineBlock[] {
  const blocks: TimelineBlock[] = []

  for (const entry of entries) {
    const last: TimelineBlock | undefined = blocks[blocks.length - 1]
    if (canMergeWith(last, entry) && last !== undefined) {
      last.entries.push(entry)
      continue
    }
    blocks.push(makeBlock(entry.kind, entry))
  }

  return blocks
}
