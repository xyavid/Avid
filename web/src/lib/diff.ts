/**
 * 行级 diff：工具输出里的真 diff 文本自己解析，`edit_file` 的参数自己生成。
 *
 * 只做行级、只认 unified 格式。字符级 diff 在长行工具输出上收益有限，却要引入
 * Myers 与一堆阈值调参；LCS 的规模上限把最坏情况钉死，超限就退化成「先整块删、
 * 再整块添」——那是给人看的 diff，不是拿来打补丁的。
 */

export type DiffLineKind = 'add' | 'del' | 'ctx' | 'meta'

export interface DiffLine {
  kind: DiffLineKind
  text: string
  oldNo: number | null
  newNo: number | null
}

type Op = { kind: 'add' | 'del' | 'ctx'; text: string }

/** LCS 的 DP 单元上限：超过它说明这不是给人看的 diff，退化成块级增删。 */
const MAX_CELLS = 40_000

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/
const FILE_HEAD = /^(?:diff |index |--- |\+\+\+ |new file|deleted file|similarity |rename |Binary files)/

function meta(text: string): DiffLine {
  return { kind: 'meta', text, oldNo: null, newNo: null }
}

/** 给一串操作补上行号：新增占新侧，删除占旧侧，上下文两侧都占。 */
function numbered(ops: Op[]): DiffLine[] {
  let oldNo = 1
  let newNo = 1
  return ops.map((op) => {
    const line: DiffLine = { kind: op.kind, text: op.text, oldNo: null, newNo: null }
    if (op.kind !== 'add') {
      line.oldNo = oldNo
      oldNo += 1
    }
    if (op.kind !== 'del') {
      line.newNo = newNo
      newNo += 1
    }
    return line
  })
}

export function parseUnifiedDiff(text: string): DiffLine[] {
  const rows = text.split('\n')
  if (rows[rows.length - 1] === '') rows.pop()
  const lines: DiffLine[] = []
  let oldNo = 1
  let newNo = 1

  for (const row of rows) {
    const line = row.endsWith('\r') ? row.slice(0, -1) : row
    const hunk = HUNK.exec(line)
    if (hunk) {
      oldNo = Number(hunk[1] ?? 1)
      newNo = Number(hunk[2] ?? 1)
      lines.push(meta(line))
      continue
    }
    if (FILE_HEAD.test(line) || line.startsWith('\\')) {
      lines.push(meta(line))
      continue
    }
    if (line.startsWith('+')) {
      lines.push({ kind: 'add', text: line.slice(1), oldNo: null, newNo })
      newNo += 1
      continue
    }
    if (line.startsWith('-')) {
      lines.push({ kind: 'del', text: line.slice(1), oldNo, newNo: null })
      oldNo += 1
      continue
    }
    lines.push({ kind: 'ctx', text: line.startsWith(' ') ? line.slice(1) : line, oldNo, newNo })
    oldNo += 1
    newNo += 1
  }
  return lines
}

export function summarizeDiff(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0
  let removed = 0
  for (const line of lines) {
    if (line.kind === 'add') added += 1
    else if (line.kind === 'del') removed += 1
  }
  return { added, removed }
}

/** 只认真正的 diff 头：`@@` 块头，或成对的 `--- / +++`（也含 `diff --git`）。 */
export function looksLikeDiff(text: string): boolean {
  if (!text) return false
  if (/^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/m.test(text)) return true
  return /^(?:--- .*\n\+\+\+ .*|diff --git .*)$/m.test(text)
}

/** 行级 LCS；规模超限时退化成「先删后添」。 */
function lcsOps(oldLines: string[], newLines: string[]): Op[] {
  const n = oldLines.length
  const m = newLines.length
  const ops: Op[] = []
  if (n === 0) return newLines.map((text) => ({ kind: 'add' as const, text }))
  if (m === 0) return oldLines.map((text) => ({ kind: 'del' as const, text }))
  if (n * m > MAX_CELLS) {
    for (const text of oldLines) ops.push({ kind: 'del', text })
    for (const text of newLines) ops.push({ kind: 'add', text })
    return ops
  }

  const width = m + 1
  const table = new Uint16Array((n + 1) * width)
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i * width + j] =
        oldLines[i] === newLines[j]
          ? (table[(i + 1) * width + j + 1] ?? 0) + 1
          : Math.max(table[(i + 1) * width + j] ?? 0, table[i * width + j + 1] ?? 0)
    }
  }

  let i = 0
  let j = 0
  while (i < n && j < m) {
    const old = oldLines[i] ?? ''
    const fresh = newLines[j] ?? ''
    if (old === fresh) {
      ops.push({ kind: 'ctx', text: old })
      i += 1
      j += 1
      continue
    }
    if ((table[(i + 1) * width + j] ?? 0) >= (table[i * width + j + 1] ?? 0)) {
      ops.push({ kind: 'del', text: old })
      i += 1
    } else {
      ops.push({ kind: 'add', text: fresh })
      j += 1
    }
  }
  while (i < n) {
    ops.push({ kind: 'del', text: oldLines[i] ?? '' })
    i += 1
  }
  while (j < m) {
    ops.push({ kind: 'add', text: newLines[j] ?? '' })
    j += 1
  }
  return ops
}

function toLines(value: unknown): string[] {
  if (typeof value !== 'string' || value === '') return []
  return value.replace(/\r\n?/g, '\n').split('\n')
}

/**
 * 从 `edit_file` 参数生成 diff：先裁掉公共前后缀，中间用 LCS 对齐。
 * `write_file` 没有 old_string，等价于「整段新增」；行号是可读的片段相对号，
 * 参数里没有偏移量，所以不假装知道文件里的绝对行号。
 */
export function diffFromEdit(args: Record<string, unknown>): DiffLine[] {
  const oldLines = toLines(args.old_string)
  const newLines = toLines(args.new_string ?? args.content)
  const path = typeof args.path === 'string' ? args.path : ''
  const head = path ? [meta(`--- a/${path}`), meta(`+++ b/${path}`)] : []
  if (oldLines.length === 0 && newLines.length === 0) return head

  let start = 0
  while (start < oldLines.length && start < newLines.length && oldLines[start] === newLines[start]) {
    start += 1
  }
  let oldEnd = oldLines.length
  let newEnd = newLines.length
  while (oldEnd > start && newEnd > start && oldLines[oldEnd - 1] === newLines[newEnd - 1]) {
    oldEnd -= 1
    newEnd -= 1
  }

  const ops: Op[] = []
  for (let i = 0; i < start; i += 1) ops.push({ kind: 'ctx', text: oldLines[i] ?? '' })
  ops.push(...lcsOps(oldLines.slice(start, oldEnd), newLines.slice(start, newEnd)))
  for (let i = oldEnd; i < oldLines.length; i += 1) ops.push({ kind: 'ctx', text: oldLines[i] ?? '' })
  return [...head, ...numbered(ops)]
}
