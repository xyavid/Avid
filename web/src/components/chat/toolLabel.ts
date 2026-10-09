/**
 * Tool call → one "verb + target" line for the timeline; unregistered tools (MCP) fall back
 * to the tool name plus compacted arguments rather than guessing semantics. Paths are
 * relativized only inside the workspace root, and with no root nothing is relativized.
 */

import { parseArgs, stringField as field, subagentTasks } from '../../state/toolArgs'
import type { IconName } from '../../ui/Icon'

export type ToolLabel = {
  icon: IconName
  /** Action word; unregistered tools fall back to the tool name. */
  verb: string
  /** Relative path, command first line, pattern or task name; empty = nothing readable. */
  target: string
}

const FALLBACK_ICON: IconName = 'file-frame'

function relativeTo(path: string, root: string | null | undefined): string {
  if (!root) return path
  const prefix = root.endsWith('/') ? root : `${root}/`
  return path.startsWith(prefix) ? path.slice(prefix.length) : path
}

function firstLine(text: string): string {
  return (text.split('\n', 1)[0] ?? '').trim()
}

function todoCount(parsed: Record<string, unknown> | null): string {
  const todos = parsed?.todos
  return Array.isArray(todos) ? `${todos.length} 项` : ''
}

export function toolLabel(name: string, args: string, root?: string | null): ToolLabel {
  const parsed = parseArgs(args)
  const text = (key: string) => field(parsed, key)
  // Invalid JSON args (partial stream, bare string) are shown as the target instead of blank.
  const raw = parsed === null ? args.trim() : ''
  const or = (value: string) => value || raw
  switch (name) {
    case 'read_file':
      return { icon: 'file-frame', verb: '读取', target: or(relativeTo(text('path'), root)) }
    case 'write_file':
      return { icon: 'pencil', verb: '写入', target: or(relativeTo(text('path'), root)) }
    case 'edit_file':
      return { icon: 'pencil', verb: '编辑', target: or(relativeTo(text('path'), root)) }
    case 'glob':
      return { icon: 'search', verb: '查找', target: or(text('pattern')) }
    case 'grep_search':
      return { icon: 'search', verb: '搜索', target: or(text('pattern')) }
    case 'bash':
      return { icon: 'terminal', verb: '执行', target: or(firstLine(text('command'))) }
    case 'todo_write':
      return { icon: 'list-checks', verb: '更新清单', target: or(todoCount(parsed)) }
    case 'load_skill':
      return { icon: 'sparkle', verb: '加载技能', target: or(text('name')) }
    case 'subagent': {
      const tasks = subagentTasks(args)
      return {
        // bot icon: `git-branch` is reserved for the branch action — do not share the mark.
        icon: 'bot',
        verb: '子智能体',
        target: or(tasks.length === 1 ? tasks[0]! : tasks.length > 1 ? `${tasks.length} 个子任务` : ''),
      }
    }
    default:
      return { icon: FALLBACK_ICON, verb: name, target: parsed === null ? args : JSON.stringify(parsed) }
  }
}
