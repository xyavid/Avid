/**
 * 工具调用 → 详情视图（展开工具卡时画什么）。
 *
 * 表格只有**文件类工具**三行，各按它自己的语义给视图：
 *   · `edit_file`  —— 参数里旧文与新文都在 → 差异视图（这是「改了什么」的真正答案）；
 *   · `read_file`  —— 结果是内容本身 → 代码视图（带语言标与行号，行号从 offset 起）；
 *   · `write_file` —— 新建时整篇都是新增（差异视图），覆盖时只画写入的新内容
 *                     （旧内容已经被写掉了，我们拿不到，不假装对比）。
 * 其余工具一律不给详情：命令输出、清单、子任务各有各的样子，回落成原文更诚实。
 *
 * 两条与内核的耦合，都写在明处：
 *   1. 结果状态为 failed 时不给视图——文件没被改动，画出来的差异是假的；
 *   2. `write_file` 的「新建 / 覆盖」认内核结果的措辞（已新建 / 已覆盖）。文案一改
 *      就认不出，那时回落原文（少一个视图），不猜错一个视图。
 */

import { langOf } from '../../markdown/langOf'
import type { ToolStatus } from '../../state/timeline'
import { parseArgs } from './toolLabel'

export type ToolDetail =
  | { kind: 'diff'; lang: string | null; before: string; after: string; note: string | null }
  | { kind: 'code'; lang: string | null; text: string; startLine: number; note: string | null }

function text(parsed: Record<string, unknown> | null, key: string): string | null {
  const value = parsed?.[key]
  return typeof value === 'string' ? value : null
}

/** 结果文本只在非空时才作注脚（运行中还没有结果、被折叠的空串都不算）。 */
function noteOf(result: string | null): string | null {
  const trimmed = (result ?? '').trim()
  return trimmed === '' ? null : trimmed
}

/** read_file 的 offset：数字或数字串都认，认不出按 1。 */
function startLineOf(parsed: Record<string, unknown> | null): number {
  const raw = parsed?.offset
  const value = typeof raw === 'number' ? raw : typeof raw === 'string' ? Number.parseInt(raw, 10) : 1
  return Number.isFinite(value) && value >= 1 ? Math.floor(value) : 1
}

export function toolDetail(
  name: string,
  args: string,
  result: string | null,
  status: ToolStatus,
): ToolDetail | null {
  if (status === 'failed') return null
  const parsed = parseArgs(args)
  const path = text(parsed, 'path')
  if (path === null) return null

  if (name === 'edit_file') {
    const before = text(parsed, 'old_string')
    const after = text(parsed, 'new_string')
    if (before === null || after === null || before === after) return null
    return { kind: 'diff', lang: langOf(path), before, after, note: noteOf(result) }
  }

  if (name === 'write_file') {
    const content = text(parsed, 'content')
    const note = noteOf(result)
    if (content === null || note === null) return null
    if (note.startsWith('已新建')) {
      return { kind: 'diff', lang: langOf(path), before: '', after: content, note }
    }
    if (note.startsWith('已覆盖')) {
      return { kind: 'code', lang: langOf(path), text: content, startLine: 1, note }
    }
    return null
  }

  if (name === 'read_file') {
    // 内容**不 trim**：首尾空行是文件的一部分，行号也要对得上（只要判空才 trim）
    if (result === null || result.trim() === '') return null
    return { kind: 'code', lang: langOf(path), text: result, startLine: startLineOf(parsed), note: null }
  }

  return null
}
