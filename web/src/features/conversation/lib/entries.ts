/**
 * 条目取数的两个纯函数：分页结果拍平、按工具调用 id 找工具结果正文。
 *
 * 两者原先都写在 `routes/ConversationRoute` 里（一个内联 `flatten` 闭包与一段
 * `view.entries.find(...)`）。它们输入相同即输出相同，却挂在 L4 的组件里——于是既没有
 * 单测，也让「条目分页长什么样」这件事在 route 里多暴露一次。
 */
import type { Entry } from '../../../api/types'
import type { TimelineEntry } from '../../../lib/timeline'

/** 分页的一页：`useEntries` 的 infinite query 把它放在 `data.pages[i]`。 */
export interface EntryPages {
  entries: Entry[]
}

/**
 * 拍平分页条目。`pages` 还没到（首次加载）时给空数组——`runStoreActions.rebuild`
 * 因此总能拿到一个数组，不必在调用点写 `?? []`。
 */
export function flattenEntries(pages: EntryPages[] | undefined): Entry[] {
  if (!pages) return []
  return pages.flatMap((page) => page.entries)
}

/**
 * 工具结果正文：检查器要显示的是工具**输出**，而活动域里的 `ToolRun` 只带状态、参数与
 * 耗时，正文挂在同一次调用的时间线条目上（按 `toolCallId` 对应）。
 *
 * 查不到给空串而不是 `undefined`：检查器显示空正文是正常的一种（工具卡被打开过、
 * 但对应条目还没随分页到达），调用点不必再分一次支。
 */
export function findToolText(entries: TimelineEntry[], toolCallId: string): string {
  const matched = entries.find(
    (entry) => entry.kind === 'tool' && entry.toolCallId === toolCallId,
  )
  return matched?.text ?? ''
}
