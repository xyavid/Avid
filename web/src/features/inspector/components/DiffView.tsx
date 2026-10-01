/*
 * DiffView：行级改动的渲染。
 *
 * 为什么在颜色之外还留 `+` / `-` 前缀：红绿是最容易在色觉障碍下失效的一对，
 * 而且本主题的 ok / danger 都是低饱和墨色，"深浅差"比"红绿差"更明显——
 * 只靠底色的 diff 会变成一道需要猜的题。前缀让它在黑白下也读得懂。
 *
 * 行号列 `select-none` + `tabular-nums`：复制改动内容时不该把行号一起带走，
 * 数字等宽则保证几百行的列不会左右抖。
 */

import type { ReactElement } from 'react'

import { cx } from '../../../ui/primitives'
import { diffLines } from '../lib/diff'
import type { DiffLine } from '../lib/diff'

export interface DiffViewProps {
  before: string
  after: string
}

const ROW_STYLE: Record<DiffLine['kind'], string> = {
  add: 'bg-ok-bg text-ok',
  del: 'bg-danger-bg text-danger',
  context: 'text-ink-muted',
}

const MARK: Record<DiffLine['kind'], string> = {
  add: '+',
  del: '-',
  context: ' ',
}

export function DiffView({ before, after }: DiffViewProps): ReactElement {
  const lines = diffLines(before, after)

  if (lines.length === 0) {
    return <p className="p-a8 text-hint text-ink-muted">两侧内容完全相同。</p>
  }

  return (
    <div className="font-mono text-hint">
      {lines.map((line, index) => (
        <div
          // 行级列表没有稳定 id（同一行内容可以出现多次），索引是这里的正确键。
          key={index}
          className={cx('flex gap-a8 whitespace-pre px-a8', ROW_STYLE[line.kind])}
        >
          <span className="w-[3.5em] shrink-0 select-none text-right text-ink-faint tabular-nums">
            {line.oldNo ?? ''}
          </span>
          <span className="w-[3.5em] shrink-0 select-none text-right text-ink-faint tabular-nums">
            {line.newNo ?? ''}
          </span>
          <span className="min-w-0 flex-1">
            {MARK[line.kind]}
            {line.text}
          </span>
        </div>
      ))}
    </div>
  )
}
