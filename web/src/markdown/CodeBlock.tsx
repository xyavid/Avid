/**
 * 代码块（阶段 33 · 阶段 9）：语言标 + 复制按钮 + 横向滚动。
 *
 * 两个可选能力来自「文件预览」（stage 52 的右列文件面板）：`path` 在最上面加一行
 * 完整路径（预览时要看清自己在看哪个文件），`lineNumbers` 在左侧加一列行号
 * （阅读代码时行号是坐标）。两处都用同一套字体与行高，行号才与代码对得上。
 *
 * 两条纪律：
 *   · 一切颜色/圆角/字号引用 token（纸面上是「凹下去的一块」，用 overlay 底 + 发丝线，
 *     不用深色主题那种黑底，免得在暖纸上砸出一个洞）；
 *   · **语言标是 svg 时不在这里处理**——图像化在 Markdown.tsx 里换成 <img>，
 *     这里只管文本形态的代码。
 */

import { useState } from 'react'

import { copyText } from './clipboard'
import { highlight, type TokenKind } from './highlight'
import { cx } from '../ui/cx'

/** token 类别 → token 色。函数/类型名不占颜色，用墨色中粗——见 highlight.ts 的色板说明。 */
const KIND_CLASS: Record<TokenKind, string> = {
  comment: 'text-syntax-comment italic',
  keyword: 'text-syntax-keyword',
  string: 'text-syntax-string',
  number: 'text-syntax-number',
  function: 'font-medium text-ink',
  plain: '',
}

export type CodeBlockProps = {
  lang: string | null
  text: string
  /** 完整路径（文件预览用）；null = 不显示这一行。 */
  path?: string | null
  /** 左侧行号列；横向滚动只滚代码，行号留在原地。 */
  lineNumbers?: boolean
  /** 行号起点（读文件的 offset 用：这一段从第 100 行读起，行号就得从 100 数）。 */
  startLine?: number
}

/** 行号列的文本（start..start+n-1）；尾随换行不额外多算一行给编号——它对应的是空行尾。 */
export function lineNumbersOf(text: string, start = 1): number[] {
  const lines = text.split('\n')
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
  return lines.map((_, index) => start + index)
}

export function CodeBlock({ lang, text, path = null, lineNumbers = false, startLine = 1 }: CodeBlockProps) {
  const [copied, setCopied] = useState(false)
  const tokens = highlight(text, lang)

  const onCopy = () => {
    void copyText(text).then((ok) => {
      if (!ok) return
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="my-a12 overflow-hidden rounded-sm border-hairline border-hair bg-overlay-subtle">
      {path !== null && (
        <div className="border-b border-hair bg-overlay-light px-a8 py-a4">
          <span className="block truncate font-mono text-micro text-ink-muted">{path}</span>
        </div>
      )}
      <div className="flex items-center justify-between border-b border-hair bg-overlay-light px-a8 py-a4">
        <span className="font-mono text-micro text-ink-muted">{lang ?? '文本'}</span>
        <button
          type="button"
          onClick={onCopy}
          className="rounded-xs px-a6 py-[1px] font-ui text-micro text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-medium hover:text-ink"
        >
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <div className="flex">
        {lineNumbers && (
          <pre
            aria-hidden
            className="shrink-0 select-none border-r border-hair px-a8 py-a8 text-right font-mono text-caption leading-[1.6] text-ink-muted"
          >
            {lineNumbersOf(text, startLine).join('\n')}
          </pre>
        )}
        <pre className="scroll-auto min-w-0 flex-1 overflow-x-auto px-a12 py-a8">
          <code className={cx('font-mono text-caption leading-[1.6] text-ink')}>
            {/* 逐 token 出文本节点：拼回去逐字等于原文（highlight.ts 的硬不变量），
                复制按钮复制的也是原文，不经过任何转换。 */}
            {tokens.map((token, i) => (
              <span key={i} className={KIND_CLASS[token.kind]}>
                {token.text}
              </span>
            ))}
          </code>
        </pre>
      </div>
    </div>
  )
}
