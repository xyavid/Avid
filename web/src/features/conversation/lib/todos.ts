/**
 * TODO 清单的解析：从 `ToolRun[]` 里回放"最近一次生效的 todo_write 调用"。
 *
 * 为什么要回放而不是让 reducer 单独存一份清单：
 * 内核的 todo 状态**不是**独立事件，它只会作为工具调用出现在时间线上。
 * 界面要显示清单，就得从工具调用里读——这与 §1.2「事件是步骤级事实」的约定一致，
 * 不给前端加第二份需要同步的状态。
 */
import type { ToolRun } from '../../../events/reducer'

export type TodoStatus = 'pending' | 'in_progress' | 'completed'

export interface TodoItem {
  id: string
  content: string
  status: TodoStatus
}

/** 内核里整份替换 TODO 的工具名（`policy/todo.py` 的 `@tool(name="todo_write")`）。 */
export const TODO_TOOL_NAME = 'todo_write'

const TODO_STATUSES: readonly TodoStatus[] = ['pending', 'in_progress', 'completed']

function isTodoStatus(value: unknown): value is TodoStatus {
  return typeof value === 'string' && (TODO_STATUSES as readonly string[]).includes(value)
}

/**
 * 解析清单参数。任何一处不合内核的校验规则就返回 null，**绝不抛**——
 * 时间线可能是重放/截断的旧数据，界面不能因为一条脏参数白屏。
 *
 * 键名只认 `todos`，依据是 `src/avid/policy/todo.py:85`（schema 的 properties 里就叫
 * `todos`）与 `:106`（`required=("todos",)`），handler 也读 `args.get("todos")`（`:113`）。
 * 那里第 88 行的 `"items"` 是 **JSON Schema 的数组元素关键字**（描述"todos 里每一项长什么样"），
 * 不是参数名；`TodoList.items` 是 Python 内部属性。两者都不是线格式字段，
 * 所以不做 `items` 别名——凭空多认一个不存在的键，只会在真出现问题时把错误藏起来。
 *
 * 空数组是合法结果（`todo_write([])` = 清空清单），与 null（"没调用过/读不出来"）区分开：
 * 前者是"清单是空的"这个事实，后者是"没有这个数"。
 */
function parseTodos(raw: unknown): TodoItem[] | null {
  let value = raw
  // 参数有时以 JSON 字符串落到时间线上（工具协议允许 arguments 是字符串），先解开再校验。
  if (typeof value === 'string') {
    try {
      value = JSON.parse(value) as unknown
    } catch {
      return null
    }
  }
  if (!Array.isArray(value)) return null

  const items: TodoItem[] = []
  for (let index = 0; index < value.length; index += 1) {
    const item: unknown = value[index]
    if (typeof item !== 'object' || item === null) return null
    const record = item as Record<string, unknown>
    const content = record['content']
    const status = record['status']
    if (typeof content !== 'string' || !isTodoStatus(status)) return null
    // 内核的 todo 项没有 id，只有 content/status；用序号合成一个稳定 key，
    // 因为 todo_write 是"整份替换"语义，序号就是这一份清单里的身份。
    items.push({ id: `todo-${index}`, content: content.trim(), status })
  }
  return items
}

/**
 * 取最近一次 todo_write 的清单；解析不出返回 null。
 *
 * "最近" = 数组里最后一个**生效**的调用（ToolRun[] 按调用顺序排列）。
 * error / denied 的调用被跳过：它们没有改动内核里的清单，拿它们当"最近状态"会显示一份
 * 实际并不存在的清单——那比显示旧清单错得更远。
 */
export function latestTodos(tools: readonly ToolRun[]): TodoItem[] | null {
  for (let index = tools.length - 1; index >= 0; index -= 1) {
    const run = tools[index]
    if (!run || run.tool !== TODO_TOOL_NAME) continue
    if (run.status === 'error' || run.status === 'denied') continue
    return parseTodos(run.args ? run.args['todos'] : undefined)
  }
  return null
}
