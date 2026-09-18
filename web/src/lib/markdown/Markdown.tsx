/**
 * Markdown 渲染：react-markdown + GFM，样式全部落在元素映射上。
 *
 * 三条取舍：
 *   · 链接走 `sanitizeUrl` 白名单，且**不设 target**——外部链接不抢用户当前的标签页，
 *     也不把 window.opener 交出去；
 *   · 代码块是唯一允许深底浅字的地方（`.term`），且不倾斜：正文和代码不参与涂鸦的
 *     旋转语言，倾斜只属于装饰外壳；
 *   · 标题用 `.ink-rule` 下划墨线、表格用 2px 墨框——形状来自构件类，不写内联样式。
 */

import { clsx } from 'clsx'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ReactNode } from 'react'

import { sanitizeUrl } from './sanitize'

function Code({ className, children }: { className?: string; children?: ReactNode }) {
  if (typeof className === 'string' && className.includes('language-')) {
    return <code className={clsx('font-mono text-xs', className)}>{children}</code>
  }
  return (
    <code className="rounded-sketch-1 border-hair border-ink bg-input px-1 font-mono text-xs">
      {children}
    </code>
  )
}

const COMPONENTS: Components = {
  h1: ({ children }) => <h1 className="ink-rule mb-2 pb-1 font-sketch text-lg">{children}</h1>,
  h2: ({ children }) => <h2 className="ink-rule mb-2 pb-1 font-sketch text-base">{children}</h2>,
  h3: ({ children }) => <h3 className="ink-rule mb-1 font-sketch text-sm">{children}</h3>,
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-4 border-accent pl-3 text-ink/70">{children}</blockquote>
  ),
  pre: ({ children }) => (
    <pre className="term my-3 overflow-x-auto rounded-sketch-2 p-3 shadow-sticker-4">{children}</pre>
  ),
  code: Code,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-bold border-ink text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-hair border-ink bg-sand px-2 py-1 text-left font-sketch">{children}</th>
  ),
  td: ({ children }) => <td className="border-hair border-ink px-2 py-1">{children}</td>,
  a: ({ href, children }) => (
    <a href={href} className="ink-rule font-sketch">
      {children}
    </a>
  ),
  img: ({ src, alt }) => (
    <img src={src} alt={alt ?? ''} className="max-w-full rounded-sketch-1 border-hair border-ink" />
  ),
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={clsx('break-anywhere text-sm leading-relaxed', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={sanitizeUrl}
        components={COMPONENTS}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
