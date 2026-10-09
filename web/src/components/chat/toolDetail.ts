/**
 * Tool call → detail view for the expanded card; only the three file tools get one:
 * `edit_file` → diff (old / new in the args), `read_file` → code (lines from offset),
 * `write_file` → diff when created, code when overwritten. A failed call gets no view (the
 * file is unchanged, so a diff would lie), and write_file reads create / overwrite from the
 * kernel's result wording — a wording change falls back to the raw text, not a wrong view.
 */

import { langOf } from '../../markdown/langOf'
import type { ToolStatus } from '../../state/timeline'
import { parseArgs } from '../../state/toolArgs'

export type ToolDetail =
  | { kind: 'diff'; lang: string | null; before: string; after: string; note: string | null }
  | { kind: 'code'; lang: string | null; text: string; startLine: number; note: string | null }

function text(parsed: Record<string, unknown> | null, key: string): string | null {
  const value = parsed?.[key]
  return typeof value === 'string' ? value : null
}

/** Result text is a footnote only when non-empty (running or elided-empty does not count). */
function noteOf(result: string | null): string | null {
  const trimmed = (result ?? '').trim()
  return trimmed === '' ? null : trimmed
}

/** read_file offset: numbers and numeric strings; anything else is 1. */
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
    // Content stays untrimmed: blank edge lines are file content and line numbers must line up.
    if (result === null || result.trim() === '') return null
    return { kind: 'code', lang: langOf(path), text: result, startLine: startLineOf(parsed), note: null }
  }

  return null
}
