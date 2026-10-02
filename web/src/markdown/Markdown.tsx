/**
 * markdown 渲染（阶段 33 · 阶段 9）。
 *
 * 设计约束（三条，都是这个仓库的既有纪律）：
 *   1. **不注入 HTML**：全程 React 元素，`dangerouslySetInnerHTML` 一次都不用——
 *      模型输出是外部内容，进 DOM 的唯一通路就是文本节点与属性；
 *   2. **SVG 走 <img> 数据地址**：`<img>` 里的 SVG 是隔离的（不执行脚本、不外链），
 *      所以模型给的图能直接看，而不用把它内联进文档；
 *   3. **样式只引用 token**：纸本语言里没有「深色代码块」，代码块是纸面上凹下去的一块
 *      （overlay 底 + 发丝线），标题走衬线栈，字重只用 400 / 500。
 *
 * 解析结果按文本记忆化：流式追加时每帧都会重渲染，但只有文本变了才重新解析。
 */

import { useMemo, useState, type ReactNode } from 'react'

import { CodeBlock } from './CodeBlock'
import { copyText } from './clipboard'
import { parseInline, type Inline } from './inline'
import { parseBlocks, type Align, type Block, type HeadingLevel, type ListItem } from './parse'

export type MarkdownProps = {
  children: string
  /** 追加在**最后一个块内部**的内容（流式光标）。挂在外面会另起一行，光标就跟丢了。 */
  trailing?: ReactNode
}

export function Markdown({ children, trailing }: MarkdownProps) {
  const blocks = useMemo(() => parseBlocks(children), [children])
  return (
    <div className="markdown">
      {blocks.map((block, i) =>
        renderBlock(block, i, i === blocks.length - 1 ? trailing : undefined),
      )}
      {blocks.length === 0 ? trailing : null}
    </div>
  )
}

function renderBlock(block: Block, key: number, trailing?: ReactNode) {
  switch (block.kind) {
    case 'paragraph':
      return (
        <p key={key} className="my-a8 break-words">
          {renderSoftBreaks(block.text)}
          {trailing}
        </p>
      )
    case 'heading': {
      const Tag = (`h${block.level}` as unknown) as 'h2'
      // 普通标题：一律左对齐。居中留给「带线小节标题」（section）——按层级写死居中
      // 会很死板，模型随手写的二级标题也会被居中。
      return (
        <Tag
          key={key}
          className={`mt-a16 mb-a8 font-serif font-medium tracking-[0.01em] text-ink first:mt-0 ${headingSize(block.level)}`}
        >
          {parseInline(block.text).map(renderInline)}
        </Tag>
      )
    }
    case 'section': {
      // 一节的开头：线在上、标题居中。线是这一节的一部分（原文里的那条 ---），
      // 所以不另画横线。
      const Tag = (`h${block.level}` as unknown) as 'h2'
      return (
        <div key={key} className="mt-a24 mb-a12 first:mt-0">
          <hr className="border-t border-hair" />
          <Tag
            className={`mt-a12 mb-a8 text-center font-serif font-medium tracking-[0.01em] text-ink ${headingSize(block.level)}`}
          >
            {parseInline(block.text).map(renderInline)}
          </Tag>
        </div>
      )
    }
    case 'code':
      return isSvgFence(block.lang, block.text) ? (
        <SvgFigure key={key} source={block.text} />
      ) : (
        <CodeBlock key={key} lang={block.lang} text={block.text} />
      )
    case 'list': {
      const Tag = block.ordered ? 'ol' : 'ul'
      return (
        <Tag
          key={key}
          start={block.ordered ? block.start : undefined}
          className={`my-a8 pl-[1.35em] ${block.ordered ? 'list-decimal' : 'list-disc'}`}
        >
          {block.items.map((item, i) => renderItem(item, i))}
        </Tag>
      )
    }
    case 'quote':
      return (
        <blockquote key={key} className="my-a12 border-l-2 border-accent pl-a12 text-ink-light">
          {block.blocks.map((b, i) => renderBlock(b, i))}
        </blockquote>
      )
    case 'table':
      return (
        <div key={key} className="scroll-auto my-a12 overflow-x-auto">
          <table className="w-full border-collapse text-ui">
            <thead>
              <tr className="bg-overlay-light">
                {block.head.map((cell, i) => (
                  <th
                    key={i}
                    className="border-hairline border-hair px-a8 py-a4 font-ui font-medium text-ink"
                    style={{ textAlign: alignOf(block.align, i) }}
                  >
                    {parseInline(cell).map(renderInline)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td
                      key={c}
                      className="border-hairline border-hair px-a8 py-a4 align-top text-ink"
                      style={{ textAlign: alignOf(block.align, c) }}
                    >
                      {parseInline(cell).map(renderInline)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    case 'hr':
      return <hr key={key} className="my-a16 border-t border-hair" />
  }
}

function renderItem(item: ListItem, key: number) {
  return (
    <li key={key} className="my-a4">
      {renderSoftBreaks(item.text)}
      {item.children.map((b, i) => renderBlock(b, i))}
    </li>
  )
}

/** 标题字号：一档文档标题、二档节标题、其余同正文标题档。 */
function headingSize(level: HeadingLevel): string {
  return level === 1 ? 'text-title' : level === 2 ? 'text-body' : 'text-ui'
}

function alignOf(align: Align[], i: number): Align {
  return align[i] ?? 'left'
}

/** 段落内换行 → <br>：模型排版里的换行保留，不在中文之间补空格。 */
function renderSoftBreaks(text: string) {
  const parts = text.split('\n')
  return parts.flatMap((part, i) =>
    i === 0 ? [parseInline(part).map(renderInline)] : [<br key={`br${i}`} />, parseInline(part).map(renderInline)],
  )
}

function renderInline(node: Inline, key: number) {
  switch (node.kind) {
    case 'text':
      return <span key={key}>{node.text}</span>
    case 'code':
      return (
        <code
          key={key}
          className="rounded-xs bg-overlay-light px-[4px] py-[1px] font-mono text-[0.92em] text-ink"
        >
          {node.text}
        </code>
      )
    case 'strong':
      // 字重只用 400 / 500（纸本纪律），所以加粗落在 medium 上而不是 bold
      return (
        <strong key={key} className="font-medium text-ink">
          {node.children.map(renderInline)}
        </strong>
      )
    case 'em':
      return <em key={key}>{node.children.map(renderInline)}</em>
    case 'del':
      return (
        <del key={key} className="text-ink-muted">
          {node.children.map(renderInline)}
        </del>
      )
    case 'link':
      return (
        <a
          key={key}
          href={node.href}
          target="_blank"
          rel="noreferrer noopener"
          className="text-accent underline decoration-accent/40 underline-offset-2 transition-colors duration-fast ease-out hover:decoration-accent"
        >
          {node.children.map(renderInline)}
        </a>
      )
  }
}

/** 语言标是 svg、或 html 块里其实是 <svg>：都按图渲染。 */
function isSvgFence(lang: string | null, text: string): boolean {
  const body = text.trimStart().toLowerCase()
  if (!body.startsWith('<svg')) return false
  return lang === null || ['svg', 'html', 'xml'].includes(lang.toLowerCase())
}

/**
 * 把模型给的 SVG 渲染成图。
 *
 * 用 `<img src="data:image/svg+xml;base64,…">` 而不是内联：`<img>` 里的 SVG 拿不到
 * 文档上下文（脚本不执行、外链不加载），这是「能看图」与「不引入执行面」之间成本最低的一刀。
 * 缺 xmlns 时补上——`<img>` 要求独立 SVG 自带命名空间，模型经常不写。
 */
function SvgFigure({ source }: { source: string }) {
  const [showSource, setShowSource] = useState(false)
  const [copied, setCopied] = useState(false)
  const src = useMemo(() => svgDataUrl(source), [source])

  return (
    <figure className="my-a12 overflow-hidden rounded-sm border-hairline border-hair bg-card">
      <div className="flex items-center justify-between border-b border-hair bg-overlay-light px-a8 py-a4">
        <span className="font-mono text-micro text-ink-muted">svg</span>
        <span className="flex items-center gap-a4">
          <button
            type="button"
            onClick={() => setShowSource((v) => !v)}
            className="rounded-xs px-a6 py-[1px] font-ui text-micro text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-medium hover:text-ink"
          >
            {showSource ? '看图' : '看源码'}
          </button>
          <button
            type="button"
            onClick={() => {
              void copyText(source).then((ok) => {
                if (!ok) return
                setCopied(true)
                window.setTimeout(() => setCopied(false), 1500)
              })
            }}
            className="rounded-xs px-a6 py-[1px] font-ui text-micro text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-medium hover:text-ink"
          >
            {copied ? '已复制' : '复制'}
          </button>
        </span>
      </div>
      {showSource ? (
        <pre className="scroll-auto overflow-x-auto px-a12 py-a8">
          <code className="font-mono text-caption leading-[1.6] text-ink">{source}</code>
        </pre>
      ) : (
        <div className="on flex items-center justify-center bg-card p-a16">
          <img src={src} alt="模型给出的图" className="max-h-[420px] max-w-full" />
        </div>
      )}
    </figure>
  )
}

function svgDataUrl(svg: string): string {
  const withNs = /<svg[^>]*\sxmlns=/i.test(svg)
    ? svg
    : svg.replace(/<svg/i, '<svg xmlns="http://www.w3.org/2000/svg"')
  const bytes = new TextEncoder().encode(withNs)
  let binary = ''
  for (const b of bytes) binary += String.fromCharCode(b)
  return `data:image/svg+xml;base64,${btoa(binary)}`
}
