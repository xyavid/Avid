/**
 * Facts inside tool arguments (nothing about presentation): parsing, field access and the subagent
 * task list, shared by the tool-row label, the detail view and the timeline model.
 * Rule: return empty when unrecognized — invalid JSON yields null, each caller picks a fallback.
 */

/** Argument JSON → object; null when it is not valid JSON or not an object. */
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

/** A string field; '' when missing or not a string. */
export function stringField(parsed: Record<string, unknown> | null, key: string): string {
  if (parsed === null) return ''
  const value = parsed[key]
  return typeof value === 'string' ? value : ''
}

/** Task descriptions of a subagent task list, in dispatch order. */
export function subagentTasks(args: string): string[] {
  const tasks = parseArgs(args)?.tasks
  if (!Array.isArray(tasks)) return []
  return tasks.flatMap((task) => {
    if (task === null || typeof task !== 'object') return []
    const description = (task as Record<string, unknown>).description
    return typeof description === 'string' ? [description] : []
  })
}
