/**
 * 待办清单：从运行视图里的工具调用推导出"当前计划"。
 *
 * 这个文件是清单的**唯一**推导点（`TodoPanel` 只消费它，不再自己解析参数），因为清单的
 * 权威副本不在这里、也不在某个接口上——它是 transcript 里 `todo_write` 那次调用的
 * `arguments.todos`：工具**每次都全量提交**整个列表，所以"最后一次合法调用"就是当前计划。
 * 于是这件事不需要新接口、不需要新存储，只需要一个想清楚的取法：
 *
 *   · 从没调过 → `null`。"没有计划"与"计划是空的"是两件事，面板对前者整块不渲染；
 *   · `todos` 不是数组 → **跳过那次调用**（服务端会拒收非法清单，被拒的调用不该覆盖
 *     上一次合法计划），全部跳过才是 `null`；
 *   · 勾掉畸形项、保留其余：历史里可能留着被拒的调用，一项坏掉不该让整块面板消失。
 *
 * 取的是数组里的**最后**一项而不是按 `seq` 排序：`tools` 的顺序就是时间线渲染它的顺序
 * （实时事件按到达顺序追加，重放按条目顺序重建），再排一次反而会与看到的顺序不一致。
 */

import type { ToolRun } from '../../../lib/timeline'

export type TodoStatus = 'pending' | 'in_progress' | 'completed'

export interface TodoItem {
  content: string
  status: TodoStatus
}

const STATUSES: readonly TodoStatus[] = ['pending', 'in_progress', 'completed']

/** 把 `arguments.todos` 收成条目表；不是数组时返回 `null`（= 这次调用不算数）。 */
function itemsOf(value: unknown): TodoItem[] | null {
  if (!Array.isArray(value)) return null
  const items: TodoItem[] = []
  for (const raw of value) {
    if (typeof raw !== 'object' || raw === null) continue
    const row = raw as Record<string, unknown>
    const { content, status } = row
    if (typeof content !== 'string' || !STATUSES.includes(status as TodoStatus)) continue
    items.push({ content, status: status as TodoStatus })
  }
  return items
}

/**
 * 当前计划；`null` = 这个会话（这条分支）从没提交过合法清单。
 * `[]` = 提交过、但清单是空的（面板同样不渲染，见 `TodoPanel`）。
 */
export function latestTodos(tools: readonly ToolRun[]): TodoItem[] | null {
  for (let index = tools.length - 1; index >= 0; index -= 1) {
    const tool = tools[index]
    if (tool?.tool !== 'todo_write') continue
    const items = itemsOf(tool.arguments.todos)
    if (items) return items
  }
  return null
}
