/**
 * 差异视图（文件类工具卡详情）：参考代码编辑器那种「一次改动长什么样」。
 *
 * 三处与参考界面同形：未改动行留作上下文、隔得远的折成「… 其余 N 行」；
 * 增删行带底色与左侧色条；表头给语言标与 +N / -M。
 *
 * 纪律：
 *   · 颜色只取 token（增 = ok 墨绿、删 = danger 深朱，都压一层浅底），不用代码高亮——
 *     差异视图要读的是「哪几行动了」，给每行再上语法色是两层信号抢同一个位置；
 *   · 复制按钮复制的是**差异全文**（`diffLines().text`：+ / - / 空格 逐行，不含省略行、
 *     不截断）——显示可以省，拿走的是完整的；
 *   · 差异行不折行、横向滚动（与 CodeBlock 同规矩）：折行会让「一行」与「一屏」错位。
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
    // 上下文行也留出 2px 色条位（透明）——不然增删行会比它们右移 2px，整片对不齐
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
  // 差异只在展开时算：一次改动两段文本，LCS 是纯函数但没必要每帧跟着时间线重算
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
          // 差异行没有身份（同一次改动里可能有多行一字不差），按下标作 key 是稳的
          <Row key={index} row={row} />
        ))}
      </div>
    </div>
  )
}
