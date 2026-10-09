/**
 * Code block: language label, copy button, horizontal scroll, plus optional path line and
 * line-number gutter (sharing the code's font metrics so numbers line up).
 * Colors, radii and font sizes come from tokens.css; SVG fences become `<img>` in
 * `Markdown.tsx`, this component renders text code only.
 */

import { useState } from 'react'

import { copyText } from './clipboard'
import { highlight, type TokenKind } from './highlight'
import { cx } from '../ui/cx'

/** Token kind → class; function/type names take ink + medium weight instead of a color. */
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
  /** Full path for file preview; null hides the row. */
  path?: string | null
  /** Line-number gutter; horizontal scrolling moves code only, numbers stay put. */
  lineNumbers?: boolean
  /** First line number (a read starting at line 100 must number from 100). */
  startLine?: number
}

/** Line numbers start..start+n-1; a single trailing newline adds no extra number. */
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
            {/* one text node per token: concat == source; copy writes the source, not fragments */}
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
