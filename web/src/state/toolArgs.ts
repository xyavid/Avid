/**
 * 工具参数里的**事实**（不涉及怎么显示）：解析 + 取字段 + subagent 的任务清单。
 *
 * 为什么单独一层：同一份参数有三个消费方——折叠行的标签（`components/chat/toolLabel`）、
 * 详情视图（`toolDetail`）与时间线模型（`state/timeline` 要用任务清单给子运行建条目）。
 * 放在 state 里，是因为时间线模型不该反过来 import 视图层的代码。
 *
 * 口径只有一条：**认不出就返回空**，不猜。参数不是合法 JSON（半截流、上游塞了裸串）
 * 时给 null，调用方各自决定回落到什么（标签回落原文，时间线就不建条目）。
 */

/** 参数 JSON → 对象；不是合法 JSON 或不是对象时给 null。 */
export function parseArgs(args: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(args)
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

/** 取一个字符串字段；缺失或不是字符串给 ''。 */
export function stringField(parsed: Record<string, unknown> | null, key: string): string {
  if (parsed === null) return ''
  const value = parsed[key]
  return typeof value === 'string' ? value : ''
}

/** subagent 任务清单的**任务名**（`description`），按派发顺序。 */
export function subagentTasks(args: string): string[] {
  const tasks = parseArgs(args)?.tasks
  if (!Array.isArray(tasks)) return []
  return tasks.flatMap((task) => {
    if (task === null || typeof task !== 'object') return []
    const description = (task as Record<string, unknown>).description
    return typeof description === 'string' ? [description] : []
  })
}
