import { clsx } from 'clsx'
import { ChevronDown, ChevronUp, Circle, CircleCheck, CircleDot } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { Button } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useUiStore } from '../../../state/uiStore'
import { latestTodos } from '../lib/todos'
import type { TodoStatus } from '../lib/todos'
import type { ToolRun } from '../../../lib/timeline'

/** 三档状态各一枚图标：空圈 / 实心点 / 打勾圈——形状不同，色盲也能分辨。 */
const ICONS: Record<TodoStatus, LucideIcon> = {
  pending: Circle,
  in_progress: CircleDot,
  completed: CircleCheck,
}

/** 颜色只做加强，语义由形状与 `sr-only` 的文字承担（不靠颜色单独表达状态）。 */
const TONES: Record<TodoStatus, string> = {
  pending: 'text-ink-muted',
  in_progress: 'text-info',
  completed: 'text-ok',
}

export interface TodoPanelProps {
  /** 这次运行的**全部**工具调用（`useRunView().tools`）：清单从最后一次 `todo_write` 推导。 */
  tools: readonly ToolRun[]
}

/**
 * 待办清单面板：常驻在输入条**正上方**，随 agent 更新计划实时变。
 *
 * 为什么在这里、为什么这样取数（阶段 27）：
 *   · 任务状态是这次对话的上下文——agent 拆出来的步骤与"进行到第几步"是边说边要看的，
 *     所以它和输入条同处一屏（`ConversationView` 底栏），而不是一个要离开对话去看的页面；
 *   · 清单的权威副本是会话条目里 `todo_write` 的调用参数，所以这里只做推导（`latestTodos`），
 *     **不加接口、不加存储**：刷新、重进会话、切分支看到的都正好是那条链上的计划；
 *   · 没有清单（或清单为空）时**整块不渲染**——"没有计划"不该在输入条上方留一条空框。
 *
 * 与时间线的关系：那次 `todo_write` 仍以工具卡形式留在历史里（它记录的是"当时提交了什么"），
 * 这块面板显示的是"现在的计划"。两者不是同一份东西，所以不合并、也不互相覆盖。
 *
 * 展开状态进界面域（`uiStore.todoExpanded`）：默认展开，收起过一次就记住，切会话不丢。
 */
export function TodoPanel({ tools }: TodoPanelProps) {
  const { t } = useTranslation()
  const expanded = useUiStore((state) => state.todoExpanded)
  const toggleTodo = useUiStore((state) => state.toggleTodo)
  const todos = latestTodos(tools)
  if (!todos || todos.length === 0) return null

  const done = todos.filter((item) => item.status === 'completed').length
  const Chevron = expanded ? ChevronUp : ChevronDown

  return (
    // `mb-3` 在面板自己身上：不渲染时连这段间距也不留（底栏只剩输入条）。
    <section className="surface-panel mb-3 flex flex-col gap-2 p-3" aria-label={t('todos.title')}>
      <Button
        variant="secondary"
        size="sm"
        className="w-full justify-between gap-2 text-left"
        aria-expanded={expanded}
        onClick={() => toggleTodo()}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 text-sm">{t('todos.title')}</span>
          <span className="truncate text-xs text-ink-muted">
            {t('todos.progress', { done, total: todos.length })}
          </span>
        </span>
        <Chevron size={16} strokeWidth={1.75} aria-hidden="true" className="shrink-0" />
      </Button>

      {expanded ? (
        <ul className="flex flex-col gap-1">
          {todos.map((item, index) => {
            const Icon = ICONS[item.status]
            return (
              // 条目没有稳定 id（工具只提交 content + status），key 用「序号 + 文字」：
              // 同一条待办在列表里换位置时仍然换 key，不会把上一项的状态画到下一项上。
              <li key={`${index}:${item.content}`} className="flex items-start gap-2 text-sm">
                <Icon
                  size={16}
                  strokeWidth={1.75}
                  aria-hidden="true"
                  className={clsx('mt-0.5 shrink-0', TONES[item.status])}
                />
                <span
                  className={clsx(
                    'min-w-0 break-anywhere',
                    item.status === 'completed' && 'text-ink-muted line-through',
                  )}
                >
                  {item.content}
                </span>
                {/* 状态不能只靠图标与颜色：读屏器到这里念的是「内容 + 状态」。 */}
                <span className="sr-only">{t(`todos.status.${item.status}`)}</span>
              </li>
            )
          })}
        </ul>
      ) : null}
    </section>
  )
}
