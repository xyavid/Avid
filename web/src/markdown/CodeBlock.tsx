/**
 * 代码块（阶段 33 · 阶段 9）：语言标 + 复制按钮 + 横向滚动。
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
}

export function CodeBlock({ lang, text }: CodeBlockProps) {
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
      <pre className="scroll-auto overflow-x-auto px-a12 py-a8">
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
  )
}
