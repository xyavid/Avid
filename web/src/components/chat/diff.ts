/**
 * Line-level diff for file-tool cards: LCS over the middle section, falling back to a
 * wholesale delete + add when the middle is too large to compute (never guessing). `rows` is
 * the display view with elision; `text` is the full diff, which is not a patch — no `@@`
 * headers and the line numbers do not line up.
 */

/** Unchanged lines kept around each change. */
export const CONTEXT = 3
/** Max rows drawn; the middle is dropped, both ends kept. */
export const MAX_ROWS = 400
/** LCS cell cap; above it the middle is replaced wholesale (~1 MB Int32 table). */
const MAX_CELLS = 250_000

export type DiffRow =
  | { kind: 'context' | 'add' | 'del'; text: string }
  /** Elided unchanged-line count (display only; absent from `text`). */
  | { kind: 'skip'; count: number }

export type LineDiff = {
  rows: DiffRow[]
  added: number
  removed: number
  /** Full diff with context lines and without elision rows. */
  text: string
}

type Op = { kind: 'context' | 'add' | 'del'; text: string }

const MARK: Record<Op['kind'], string> = { context: ' ', add: '+', del: '-' }

/** Split lines; a trailing newline does not create an empty last line. */
function splitLines(text: string): string[] {
  if (text === '') return []
  const lines = text.split('\n')
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
  return lines
}

/** Mid-section LCS backtrack; over MAX_CELLS it becomes wholesale delete + add. */
function diffMiddle(a: string[], b: string[]): Op[] {
  if (a.length === 0) return b.map((text) => ({ kind: 'add' as const, text }))
  if (b.length === 0) return a.map((text) => ({ kind: 'del' as const, text }))
  if (a.length * b.length > MAX_CELLS) {
    return [
      ...a.map((text) => ({ kind: 'del' as const, text })),
      ...b.map((text) => ({ kind: 'add' as const, text })),
    ]
  }

  const width = b.length + 1
  const lcs = new Int32Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      lcs[i * width + j] =
        a[i] === b[j]
          ? lcs[(i + 1) * width + j + 1]! + 1
          : Math.max(lcs[(i + 1) * width + j]!, lcs[i * width + j + 1]!)
    }
  }

  const ops: Op[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      ops.push({ kind: 'context', text: a[i]! })
      i += 1
      j += 1
    } else if (lcs[(i + 1) * width + j]! >= lcs[i * width + j + 1]!) {
      ops.push({ kind: 'del', text: a[i]! })
      i += 1
    } else {
      ops.push({ kind: 'add', text: b[j]! })
      j += 1
    }
  }
  while (i < a.length) {
    ops.push({ kind: 'del', text: a[i]! })
    i += 1
  }
  while (j < b.length) {
    ops.push({ kind: 'add', text: b[j]! })
    j += 1
  }
  return ops
}

function diffOps(a: string[], b: string[]): Op[] {
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head += 1
  let tail = 0
  while (
    tail < a.length - head &&
    tail < b.length - head &&
    a[a.length - 1 - tail] === b[b.length - 1 - tail]
  ) {
    tail += 1
  }

  const ops: Op[] = a.slice(0, head).map((text) => ({ kind: 'context' as const, text }))
  ops.push(...diffMiddle(a.slice(head, a.length - tail), b.slice(head, b.length - tail)))
  ops.push(...a.slice(a.length - tail).map((text) => ({ kind: 'context' as const, text })))
  return ops
}

/** Elides long context runs: keep `CONTEXT` lines adjacent to each change. */
function elide(ops: Op[]): DiffRow[] {
  const rows: DiffRow[] = []
  let i = 0
  while (i < ops.length) {
    const op = ops[i]!
    if (op.kind !== 'context') {
      rows.push(op)
      i += 1
      continue
    }
    let end = i
    while (end < ops.length && ops[end]!.kind === 'context') end += 1
    const run = ops.slice(i, end)
    const lead = i === 0
    const trail = end === ops.length

    if (!lead && !trail && run.length <= CONTEXT * 2 + 1) {
      rows.push(...run) // The run is short and touches changes on both sides; eliding would fragment it.
    } else if (lead || trail) {
      // Leading / trailing runs keep only the `CONTEXT` lines adjacent to the change.
      const kept = lead ? run.slice(Math.max(0, run.length - CONTEXT)) : run.slice(0, CONTEXT)
      const skip = run.length - kept.length
      if (lead) rows.push(...(skip > 0 ? [{ kind: 'skip' as const, count: skip }] : []), ...kept)
      else rows.push(...kept, ...(skip > 0 ? [{ kind: 'skip' as const, count: skip }] : []))
    } else {
      const head = run.slice(0, CONTEXT)
      const keptTail = run.slice(run.length - CONTEXT)
      rows.push(...head, { kind: 'skip', count: run.length - head.length - keptTail.length }, ...keptTail)
    }
    i = end
  }
  return rows
}

/** Over MAX_ROWS: keep both ends and fold the dropped lines into one skip row. */
function cap(rows: DiffRow[]): DiffRow[] {
  if (rows.length <= MAX_ROWS) return rows
  const head = Math.floor(MAX_ROWS / 2) - 1
  const tail = MAX_ROWS - head - 1
  const dropped = rows.slice(head, rows.length - tail)
  const hidden = dropped.reduce((sum, row) => sum + (row.kind === 'skip' ? row.count : 1), 0)
  return [...rows.slice(0, head), { kind: 'skip', count: hidden }, ...rows.slice(rows.length - tail)]
}

/** Two texts → diff rows; unchanged input yields empty `rows` and ''. */
export function diffLines(before: string, after: string): LineDiff {
  const ops = diffOps(splitLines(before), splitLines(after))
  const added = ops.filter((op) => op.kind === 'add').length
  const removed = ops.filter((op) => op.kind === 'del').length
  return {
    rows: added === 0 && removed === 0 ? [] : cap(elide(ops)),
    added,
    removed,
    text: added === 0 && removed === 0 ? '' : ops.map((op) => `${MARK[op.kind]}${op.text}`).join('\n'),
  }
}
