/**
 * 行级差异（文件类工具卡详情用）：把「改之前」与「改之后」两段文本算成一屏能读的差异行。
 *
 * 为什么自研而不是引 diff/jest-diff：这里只要「行级、给人眼、能省略」这一件事，
 * 全套 diff 库（含字符级、补丁格式、样式）是几十 kB 的首屏代价，而这台界面
 * 已经有体积预算（`web/budget.json`）。算法是标准 LCS：先掐掉两端相同的行
 * （编辑通常只动中间一段），中间用最长公共子序列走一遍；中间大到算不动
 * （见 `MAX_CELLS`）就整段删、整段加——宁可给粗答案，不猜。
 *
 * 省略规则（与参考界面同形）：未改动的行超过 `CONTEXT * 2 + 1` 行才折，
 * 折出来的那一行写「… 其余 N 行」。
 *
 * 显示与复制分开：`rows` 是给人看的（带省略、有上限），`text` 是原样的差异全文
 * （+ / - / 空格 逐行，不含省略行、不截断），复制按钮复制的就是它。它**不是补丁**：
 * 没有 @@ 头，行号也不对（这段只是 old/new 两个片段，不是整个文件）——别拿它去 patch。
 */

/** 改动上下各留几行未改动行。 */
export const CONTEXT = 3
/** 一屏最多画多少行（超了删中间、留两头）。 */
export const MAX_ROWS = 400
/** LCS 表的格子上限：超了就走「整段删 + 整段加」的粗答案（约 1 MB 的 Int32 表）。 */
const MAX_CELLS = 250_000

export type DiffRow =
  | { kind: 'context' | 'add' | 'del'; text: string }
  /** 省略的未改动行数（显示用；`text` 里没有它）。 */
  | { kind: 'skip'; count: number }

export type LineDiff = {
  rows: DiffRow[]
  added: number
  removed: number
  /** 差异全文（含未改动的上下文行，不含省略行）。 */
  text: string
}

type Op = { kind: 'context' | 'add' | 'del'; text: string }

const MARK: Record<Op['kind'], string> = { context: ' ', add: '+', del: '-' }

/** 拆行：尾随换行造成的空尾行不是内容（"a\n" 是一行，不是两行）。 */
function splitLines(text: string): string[] {
  if (text === '') return []
  const lines = text.split('\n')
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
  return lines
}

/** 中间段的行差异：LCS 回溯。段落大到算不动就整段换（不猜中间哪几行没动）。 */
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

/** 折长段未改动行：段首 / 段尾只留贴着改动的 `CONTEXT` 行，中间段两头各留 `CONTEXT` 行。 */
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
      rows.push(...run) // 两头都够得着改动，折了反而更碎
    } else if (lead || trail) {
      // 段首留最后几行、段尾留头几行——留下的都是贴着改动的那一侧
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

/** 上限：超了删中间留两头，被删掉的行数并进一行省略（省略行自己的数也并进去）。 */
function cap(rows: DiffRow[]): DiffRow[] {
  if (rows.length <= MAX_ROWS) return rows
  const head = Math.floor(MAX_ROWS / 2) - 1
  const tail = MAX_ROWS - head - 1
  const dropped = rows.slice(head, rows.length - tail)
  const hidden = dropped.reduce((sum, row) => sum + (row.kind === 'skip' ? row.count : 1), 0)
  return [...rows.slice(0, head), { kind: 'skip', count: hidden }, ...rows.slice(rows.length - tail)]
}

/**
 * 两段文本 → 差异行。一字未改时 `rows` 为空（没改动就不铺原文）、`text` 为 ''。
 */
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
