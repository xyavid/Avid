/**
 * 任务清单面板：把 `todo_write` 最近一次提交的清单画出来。
 *
 * 三种状态各有明确的形状，不靠颜色单独区分（色觉障碍下仍可读）：
 *   空心圈 = 未开始、accent 圈 + 呼吸 = 进行中、勾 = 已完成。
 */
import type { ReactElement } from 'react'

import { Badge, cx } from '../../../ui/primitives'
import { CheckIcon } from '../../../ui/icons'
import type { TodoItem, TodoStatus } from '../lib/todos'

export interface TodoPanelProps {
  todos: TodoItem[]
}

const RING = 'mt-a4 h-a8 w-a8 shrink-0 rounded-full'

function TodoMark({ status }: { status: TodoStatus }): ReactElement {
  if (status === 'completed') {
    return <CheckIcon size={14} className="mt-a2 shrink-0 text-ink-faint" />
  }
  if (status === 'in_progress') {
    // accent 圈 + 呼吸：只动透明度（tokens 里的 paper-pulse 不含 scale），符合"不弹跳"。
    return (
      <span
        aria-hidden="true"
        className={cx(RING, 'animate-pulse border border-accent bg-accent-soft')}
      />
    )
  }
  return <span aria-hidden="true" className={cx(RING, 'border border-ink-faint/50')} />
}

export function TodoPanel({ todos }: TodoPanelProps): ReactElement | null {
  // 没有清单就整块不画：一块空面板占掉正文宽度却不给任何信息。
  if (todos.length === 0) return null

  const done = todos.filter((item) => item.status === 'completed').length

  return (
    <section
      aria-label="任务清单"
      className="rounded-sm border-hair bg-card px-a12 py-a8"
    >
      <div className="flex items-center gap-a8">
        <span className="font-medium text-ui text-ink">任务</span>
        <Badge tone="neutral">
          {done}/{todos.length}
        </Badge>
      </div>
      <ul className="mt-a6 flex flex-col gap-a4">
        {todos.map((item) => (
          <li key={item.id} className="flex items-start gap-a8">
            <TodoMark status={item.status} />
            <span
              className={cx(
                'min-w-0 text-ui text-ink-light',
                item.status === 'completed' && 'text-ink-faint line-through',
              )}
            >
              {item.content}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
