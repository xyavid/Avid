/**
 * Diff view for file-tool card details: context lines kept, distant runs elided, add / del
 * rows tinted and marked. Colors come from tokens only (add = ok, del = danger); the copy
 * button takes the full `diffLines().text` (elision and truncation are display-only), and
 * rows scroll horizontally instead of wrapping.
 */

import { useMemo, useState } from 'react'

import { copyText } from '../../markdown/clipboard'
import { cx } from '../../ui/cx'
import type { DiffRow } from './diff'
import { diffLines } from './diff'

export type DiffViewProps = {
  before: string
  after: string
  lang: string | null
}

const ROW_CLASS: Record<'context' | 'add' | 'del', string> = {
  context: 'border-transparent',
  add: 'border-ok bg-ok/10',
  del: 'border-danger bg-danger/10',
}

const MARK_CLASS: Record<'context' | 'add' | 'del', string> = {
  context: 'text-ink-muted',
  add: 'text-ok',
  del: 'text-danger',
}

function Row({ row }: { row: DiffRow }) {
  if (row.kind === 'skip') {
    return (
      <div data-diff="skip" className="border-l-2 border-transparent px-a8 font-ui text-hint text-ink-muted">
        {`… 其余 ${row.count} 行`}
      </div>
    )
  }
  const mark = row.kind === 'add' ? '+' : row.kind === 'del' ? '-' : ' '
  return (
    // Context rows keep a transparent 2px bar so add / del rows stay aligned.
    <div data-diff={row.kind} className={cx('border-l-2 px-a8 whitespace-pre', ROW_CLASS[row.kind])}>
      <span aria-hidden className={MARK_CLASS[row.kind]}>
        {mark}
      </span>
      {row.text}
    </div>
  )
}

export function DiffView({ before, after, lang }: DiffViewProps) {
  const [copied, setCopied] = useState(false)
  // The card only mounts this view when expanded, so the LCS runs then, not on every render.
  const diff = useMemo(() => diffLines(before, after), [before, after])

  const onCopy = () => {
    void copyText(diff.text).then((ok) => {
      if (!ok) return
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="my-a12 overflow-hidden rounded-sm border-hairline border-hair bg-overlay-subtle">
      <div className="flex items-center justify-between border-b border-hair bg-overlay-light px-a8 py-a4">
        <span className="flex items-center gap-a6 font-mono text-micro">
          <span className="text-ink-muted">{lang ?? '文本'}</span>
          {diff.added > 0 && <span className="text-ok">{`+${diff.added}`}</span>}
          {diff.removed > 0 && <span className="text-danger">{`-${diff.removed}`}</span>}
        </span>
        <button
          type="button"
          onClick={onCopy}
          className="rounded-xs px-a6 py-[1px] font-ui text-micro text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-medium hover:text-ink"
        >
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <div className="scroll-auto overflow-x-auto py-a4 font-mono text-caption leading-[1.6] text-ink">
        {diff.rows.map((row, index) => (
          // Diff rows have no identity (duplicate lines), so the index is the stable key.
          <Row key={index} row={row} />
        ))}
      </div>
    </div>
  )
}
