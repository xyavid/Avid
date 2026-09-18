import type { TimelineEntry, ToolRun } from '../../../lib/timeline'
// 阈值单点来自 L1 patterns：失败或拒绝的调用不进组（它们需要单独被看见）。
import { EVENT_GROUP_MIN_SIZE } from '../../../ui/patterns'

export type TimelineBlock =
  | { kind: 'entry'; id: string; entry: TimelineEntry; shapeIndex: number }
  | { kind: 'tool'; id: string; run: ToolRun; content: string }
  | { kind: 'group'; id: string; runs: ToolRun[]; contents: Record<string, string> }

function groupable(run: ToolRun | undefined): boolean {
  return run !== undefined && run.status !== 'failed' && run.status !== 'denied'
}

/**
 * 把「条目 + 工具运行」编织成可渲染的顺序：
 * assistant 条目后面跟它声明的工具调用；连续 ≥2 个正常调用收进一个组。
 *
 * 正文为空的 assistant 回合不渲染卡片，但它声明的工具调用照旧落在原位——
 * 这类回合是「只声明调用、没有说话」，真实会话里占相当比例。
 */
export function groupTimeline(
  entries: TimelineEntry[],
  tools: ToolRun[],
): TimelineBlock[] {
  const contents: Record<string, string> = {}
  for (const entry of entries) {
    if (entry.kind === 'tool' && entry.toolCallId) contents[entry.toolCallId] = entry.text
  }
  const byId = new Map(tools.map((run) => [run.toolCallId, run]))
  const placed = new Set<string>()
  const blocks: TimelineBlock[] = []
  // 形状轮换按**全部**条目计数（不是可见窗口），所以「加载更早」不会让已渲染的卡片换形。
  let entryOrdinal = 0

  for (const entry of entries) {
    if (entry.kind === 'tool') continue
    // 只声明工具调用、正文为空的 assistant 回合**不占一张卡**：那张卡除了一个角色名
    // 什么都没有（真实会话里 21 条条目有 8 条是这种），而这次调用由下面的工具卡承担。
    // 但它必须留在 entries 里——工具卡是挂到「声明它的那个条目」上的，条目一删，工具卡
    // 就会掉到时间线末尾（见文件末尾那条兜底）。
    const silent = entry.kind === 'assistant' && entry.text.trim() === ''
    if (!silent) {
      blocks.push({ kind: 'entry', id: entry.id, entry, shapeIndex: entryOrdinal })
      // 形状计数只对**真正渲染出来的**卡片递增：跳过的条目若也占号，相邻两张可见
      // 卡片可能拿到同一个形状（连续跳过两个就撞上了）。
      entryOrdinal += 1
    }
    const calls = entry.toolCalls ?? []
    const runs = calls
      .map((call) => byId.get(call.toolCallId))
      .filter((run): run is ToolRun => run !== undefined)
    runs.forEach((run) => placed.add(run.toolCallId))

    const good = runs.filter(groupable)
    const rest = runs.filter((run) => !groupable(run))
    if (good.length >= EVENT_GROUP_MIN_SIZE) {
      blocks.push({
        kind: 'group',
        id: `group:${entry.id}`,
        runs: good,
        contents,
      })
    } else {
      good.forEach((run) =>
        blocks.push({ kind: 'tool', id: `tool:${run.toolCallId}`, run, content: contents[run.toolCallId] ?? '' }),
      )
    }
    rest.forEach((run) =>
      blocks.push({ kind: 'tool', id: `tool:${run.toolCallId}`, run, content: contents[run.toolCallId] ?? '' }),
    )
  }

  // 没能挂到任何 assistant 条目上的工具（例如刷新后只拿到运行状态）单独成块。
  for (const run of tools) {
    if (placed.has(run.toolCallId)) continue
    blocks.push({ kind: 'tool', id: `tool:${run.toolCallId}`, run, content: contents[run.toolCallId] ?? '' })
  }

  return blocks
}
