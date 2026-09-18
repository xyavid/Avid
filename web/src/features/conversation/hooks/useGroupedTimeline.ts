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
 */
export function useGroupedTimeline(
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
    blocks.push({ kind: 'entry', id: entry.id, entry, shapeIndex: entryOrdinal })
    entryOrdinal += 1
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
