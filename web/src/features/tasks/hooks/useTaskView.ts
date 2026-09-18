/**
 * 任务板的取数层（L2）：useTaskList 的结果按 taskFilter 过滤 + 计数。
 *
 * 过滤与计数是纯函数，单独导出便于单测；组件只消费派生结果，不自己再算一遍。
 * 任务板只读，所以这里没有任何写操作与本地副本。
 */

import { useTaskList } from '../../../api/queries'
import { useUiStore } from '../../../state/uiStore'
import type { UiState } from '../../../state/uiStore'
import type { Task } from '../../../api/types'

export type TaskFilter = UiState['taskFilter']

export interface TaskCounts {
  all: number
  pending: number
  in_progress: number
  completed: number
  blocked: number
}

export interface TaskView {
  filter: TaskFilter
  tasks: Task[]
  counts: TaskCounts
  pending: boolean
  error: unknown
  refetch: () => void
}

/** 筛选按钮的顺序：徽标计数与列表都按这个顺序读。 */
export const TASK_FILTERS: TaskFilter[] = [
  'all',
  'pending',
  'in_progress',
  'completed',
  'blocked',
]

/** 纯函数：被阻塞只看 blocked 标记，其余按状态匹配；all 原样返回。 */
export function filterTasks(tasks: Task[], filter: TaskFilter): Task[] {
  if (filter === 'all') return tasks
  if (filter === 'blocked') return tasks.filter((task) => task.blocked)
  return tasks.filter((task) => task.status === filter)
}

/** 纯函数：一次遍历统计各状态与阻塞数，避免组件里多次 filter。 */
export function taskCounts(tasks: Task[]): TaskCounts {
  const counts: TaskCounts = {
    all: tasks.length,
    pending: 0,
    in_progress: 0,
    completed: 0,
    blocked: 0,
  }
  for (const task of tasks) {
    counts[task.status] += 1
    if (task.blocked) counts.blocked += 1
  }
  return counts
}

export function useTaskView(): TaskView {
  const filter = useUiStore((state) => state.taskFilter)
  const query = useTaskList()
  const tasks = query.data ?? []

  return {
    filter,
    tasks: filterTasks(tasks, filter),
    counts: taskCounts(tasks),
    pending: query.isPending,
    error: query.error,
    refetch: query.refetch,
  }
}
