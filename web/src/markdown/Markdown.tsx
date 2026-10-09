/**
 * Renders the `parse.ts` model as React elements; no HTML is ever injected
 * (`dangerouslySetInnerHTML` is unused) and every class comes from a tokens.css token.
 * Deliberate deviations: soft breaks render as line breaks, and external images become links
 * because CSP `img-src` allows self/data only.
 */

import { useMemo, useState, type ReactNode } from 'react'

import { Icon } from '../ui/Icon'
import { CodeBlock } from './CodeBlock'
import { copyText } from './clipboard'
import type { Align, Block, HeadingLevel, Inline, ListItem } from './parse'
import { parseBlocks } from './parse'

export type MarkdownProps = {
  children: string
  /** Appended inside the last block (streaming cursor); outside it would start its own line. */
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
          {renderInline(block.inline)}
          {trailing}
        </p>
      )
    case 'heading': {
      const Tag = (`h${block.level}` as unknown) as 'h2'
      // Plain headings stay left-aligned; centering is reserved for `section` blocks.
      return (
        <Tag
          key={key}
          className={`mt-a16 mb-a8 font-serif font-medium tracking-[0.01em] text-ink first:mt-0 ${headingSize(block.level)}`}
        >
          {renderInline(block.inline)}
          {trailing}
        </Tag>
      )
    }
    case 'section': {
      // A section opens here: rule above a centered heading — the rule is the original `---`.
      const Tag = (`h${block.level}` as unknown) as 'h2'
      return (
        <div key={key} className="mt-a24 mb-a12 first:mt-0">
          <hr className="border-t border-hair" />
          <Tag
            className={`mt-a12 mb-a8 text-center font-serif font-medium tracking-[0.01em] text-ink ${headingSize(block.level)}`}
          >
            {renderInline(block.inline)}
            {trailing}
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
                    {renderInline(cell)}
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
                      {renderInline(cell)}
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
    case 'literal':
      // Unrecognized block (HTML etc.): plain text, keeping its own newlines.
      return (
        <p key={key} className="my-a8 whitespace-pre-wrap break-words font-mono text-caption text-ink-muted">
          {block.text}
        </p>
      )
  }
}

function renderItem(item: ListItem, key: number) {
  // A single-paragraph item skips <p> on purpose: tight and loose lists look the same.
  const only = item.blocks.length === 1 && item.blocks[0]?.kind === 'paragraph' ? item.blocks[0] : null
  return (
    <li key={key} className="my-a4">
      {item.checked !== null && <Checkbox checked={item.checked} />}
      {only !== null ? renderInline(only.inline) : item.blocks.map((b, i) => renderBlock(b, i))}
    </li>
  )
}

/** GFM task box, drawn rather than a native checkbox. */
function Checkbox({ checked }: { checked: boolean }) {
  return (
    <span
      role="img"
      aria-label={checked ? '已完成' : '未完成'}
      className="mr-a6 inline-flex h-[13px] w-[13px] translate-y-[1px] items-center justify-center rounded-xs border-hairline border-hair text-accent"
    >
      {checked && <Icon name="check" size={10} />}
    </span>
  )
}

/** Heading sizes; h5/h6 match body size and differ by weight and serif (tokens in tokens.css). */
function headingSize(level: HeadingLevel): string {
  switch (level) {
    case 1:
      return 'text-chat-h1'
    case 2:
      return 'text-chat-h2'
    case 3:
      return 'text-chat-h3'
    case 4:
      return 'text-chat-h4'
    default:
      return 'text-chat font-medium'
  }
}

function alignOf(align: Align[], i: number): Align {
  return align[i] ?? 'left'
}

function renderInline(nodes: Inline[]): ReactNode {
  return nodes.map((node, i) => {
    switch (node.kind) {
      case 'text':
        return <span key={i}>{node.text}</span>
      case 'code':
        return (
          <code
            key={i}
            className="rounded-xs bg-overlay-light px-[4px] py-[1px] font-mono text-[0.92em] text-ink"
          >
            {node.text}
          </code>
        )
      case 'strong':
        // Weights are 400/500 only, so bold lands on medium, not bold
        return (
          <strong key={i} className="font-medium text-ink">
            {renderInline(node.children)}
          </strong>
        )
      case 'em':
        return <em key={i}>{renderInline(node.children)}</em>
      case 'del':
        return (
          <del key={i} className="text-ink-muted">
            {renderInline(node.children)}
          </del>
        )
      case 'link':
        return (
          <a
            key={i}
            href={node.href}
            target="_blank"
            rel="noreferrer noopener"
            className="text-accent underline decoration-accent/40 underline-offset-2 transition-colors duration-fast ease-out hover:decoration-accent"
          >
            {renderInline(node.children)}
          </a>
        )
      case 'image':
        return inlineImage(node.src, node.alt, i)
      case 'break':
      case 'softbreak':
        return <br key={i} />
    }
  })
}

/**
 * Images: `data:` sources draw inline, external ones become links on purpose — CSP `img-src`
 * allows self/data only, and remote images are ready-made tracking pixels.
 */
function inlineImage(src: string, alt: string, key: number) {
  if (src.startsWith('data:') && src.startsWith('data:image/')) {
    return <img key={key} src={src} alt={alt} className="my-a4 max-h-[420px] max-w-full rounded-sm" />
  }
  return (
    <a
      key={key}
      href={src}
      target="_blank"
      rel="noreferrer noopener"
      className="text-accent underline decoration-accent/40 underline-offset-2 transition-colors duration-fast ease-out hover:decoration-accent"
    >
      {alt || src}
    </a>
  )
}

/** `svg` fence, or an `html` fence whose body is an `<svg>`: both render as a figure. */
function isSvgFence(lang: string | null, text: string): boolean {
  const body = text.trimStart().toLowerCase()
  if (!body.startsWith('<svg')) return false
  return lang === null || ['svg', 'html', 'xml'].includes(lang.toLowerCase())
}

/**
 * SVG from the model, drawn via an `<img>` data URL so it cannot run scripts or load externals;
 * a missing `xmlns` is added because `<img>` needs a standalone SVG.
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
