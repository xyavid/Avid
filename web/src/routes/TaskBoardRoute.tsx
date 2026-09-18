import { TaskBoard } from '../features/tasks'

/** L4：任务板（只读）。任务图的写入者有且只有 agent 的任务工具。 */
export function TaskBoardRoute() {
  return (
    <div className="scroll-area min-h-0 min-w-0 flex-1">
      <TaskBoard />
    </div>
  )
}
