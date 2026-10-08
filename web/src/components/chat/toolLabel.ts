/**
 * 工具调用 → 一行「动作 + 目标」（时间线上的工具行）。
 *
 * 为什么不直接显示参数 JSON：过程要读的是「它做了什么、对谁做的」，参数是细节，
 * 点开卡片就有。未登记的工具（MCP 等）回落成「工具名 + 压缩参数」——宁可难看，
 * 不猜它的语义。
 *
 * 路径相对化只在工作区根以内发生：根以外的绝对路径原样显示，把它显示成相对路径
 * 会指向另一个文件。根由调用方下发（会话的工作区根），无根时一律原样。
 */

import { parseArgs, stringField as field, subagentTasks } from '../../state/toolArgs'
import type { IconName } from '../../ui/Icon'

export type ToolLabel = {
  icon: IconName
  /** 动作词：读取 / 写入 / 编辑 / 执行 / 查找 / 搜索 / 子智能体…；未登记工具回落成工具名。 */
  verb: string
  /** 目标：相对路径、命令首行、pattern、任务名。空串 = 这条调用没有可读目标。 */
  target: string
}

/** 未登记工具的图标：通用的文件帧。 */
const FALLBACK_ICON: IconName = 'file-frame'

function relativeTo(path: string, root: string | null | undefined): string {
  if (!root) return path
  const prefix = root.endsWith('/') ? root : `${root}/`
  return path.startsWith(prefix) ? path.slice(prefix.length) : path
}

/** 多行命令只看首行：折叠行只有一行位置，剩下的进展开态。 */
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
  // 参数不是合法 JSON（半截流、上游塞了裸串）时原样当目标：宁可难看，不能空着让人猜。
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
    case 'bash':
      return { icon: 'terminal', verb: '执行', target: or(firstLine(text('command'))) }
    case 'todo_write':
      return { icon: 'list-checks', verb: '更新清单', target: or(todoCount(parsed)) }
    case 'load_skill':
      return { icon: 'sparkle', verb: '加载技能', target: or(text('name')) }
    case 'subagent': {
      const tasks = subagentTasks(args)
      return {
        icon: 'git-branch',
        verb: '子智能体',
        target: or(tasks.length === 1 ? tasks[0]! : tasks.length > 1 ? `${tasks.length} 个子任务` : ''),
      }
    }
    default:
      return { icon: FALLBACK_ICON, verb: name, target: parsed === null ? args : JSON.stringify(parsed) }
  }
}
