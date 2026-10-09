/**
 * Markdown → render model: syntax recognition is delegated to `@lezer/markdown`
 * (CommonMark + GFM), this module only translates its tree into `Block` / `Inline`.
 * Deliberate deviations: soft breaks render as line breaks, entities decode numeric
 * forms plus a small named table, and unrecognized nodes stay literal text.
 */

import type { SyntaxNode } from '@lezer/common'
import { GFM, parser } from '@lezer/markdown'

const md = parser.configure([GFM])

export type Align = 'left' | 'center' | 'right'

/** Heading level (1–6). */
export type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'strong'; children: Inline[] }
  | { kind: 'em'; children: Inline[] }
  | { kind: 'del'; children: Inline[] }
  | { kind: 'link'; href: string; children: Inline[] }
  /** `data:` sources render as an image; external ones become links in the render layer. */
  | { kind: 'image'; src: string; alt: string }
  /** Hard break (two trailing spaces or a trailing backslash). */
  | { kind: 'break' }
  /** Soft break; rendering it as a line break is a deliberate deviation (see module note). */
  | { kind: 'softbreak' }

export type ListItem = {
  /** GFM task state; null for a plain list item. */
  checked: boolean | null
  blocks: Block[]
}

export type Block =
  | { kind: 'paragraph'; inline: Inline[] }
  | { kind: 'heading'; level: HeadingLevel; inline: Inline[] }
  /** Section heading: a `---` plus the heading after it, rendered as a rule over centered text. */
  | { kind: 'section'; level: HeadingLevel; inline: Inline[] }
  | { kind: 'code'; lang: string | null; text: string }
  | { kind: 'list'; ordered: boolean; start: number; items: ListItem[] }
  | { kind: 'quote'; blocks: Block[] }
  | { kind: 'table'; align: Align[]; head: Inline[][]; rows: Inline[][][] }
  | { kind: 'hr' }
  /** Unrecognized block (HTML etc.): displayed as plain text, never injected. */
  | { kind: 'literal'; text: string }

type Ref = { href: string; title: string | null }

/** Inline syntax markers: not content, and skipping them must not leak their raw text. */
const SYNTAX_NODES = new Set([
  'LinkMark',
  'LinkLabel',
  'LinkTitle',
  'CodeMark',
  'EmphasisMark',
  'StrikethroughMark',
  'HeaderMark',
  'TaskMarker',
])

/** Block-level syntax markers: never content. */
const BLOCK_SYNTAX = new Set(['ListMark', 'TaskMarker', 'QuoteMark', 'CodeMark'])

/** Syntax only inside links/images; a bare URL outside is a GFM autolink. */
const DEST_NODES = new Set(['URL', 'LinkTitle'])

/** Markers whose single following space is syntax, not content. */
const MARKER_TRIM = new Set(['HeaderMark', 'TaskMarker', 'ListMark'])

export function parseBlocks(text: string): Block[] {
  const tree = md.parse(text)
  const refs = collectRefs(tree.topNode, text)
  return blocksOf(tree.topNode, text, refs)
}

/** Reference definitions apply document-wide, hence the scan; they produce no content. */
function collectRefs(root: SyntaxNode, doc: string): Map<string, Ref> {
  const refs = new Map<string, Ref>()
  for (const child of children(root)) {
    if (child.name !== 'LinkReference') continue
    const label = child.getChild('LinkLabel')
    const url = child.getChild('URL')
    if (label === null || url === null) continue
    const title = child.getChild('LinkTitle')
    refs.set(normalizeLabel(labelText(doc.slice(label.from, label.to))), {
      href: doc.slice(url.from, url.to),
      title: title === null ? null : unquote(doc.slice(title.from, title.to)),
    })
  }
  return refs
}

/** CommonMark label matching: collapse whitespace, ignore case. */
function normalizeLabel(label: string): string {
  return label.trim().replace(/\s+/g, ' ').toLowerCase()
}

function labelText(raw: string): string {
  return raw.replace(/^\[|\]$/g, '')
}

function unquote(text: string): string {
  const trimmed = text.trim()
  const first = trimmed[0]
  if (trimmed.length >= 2 && (first === '"' || first === "'" || first === '(')) {
    return trimmed.slice(1, -1)
  }
  return trimmed
}

function children(node: SyntaxNode): SyntaxNode[] {
  const out: SyntaxNode[] = []
  for (let child = node.firstChild; child !== null; child = child.nextSibling) out.push(child)
  return out
}

function headingLevel(name: string): HeadingLevel | null {
  const match = /^(?:ATX|Setext)Heading([1-6])$/.exec(name)
  return match === null ? null : (Number(match[1]) as HeadingLevel)
}

// ---------------------------------------------------------------- block level

function blocksOf(parent: SyntaxNode, doc: string, refs: Map<string, Ref>): Block[] {
  const nodes = children(parent).filter((child) => !BLOCK_SYNTAX.has(child.name))
  const blocks: Block[] = []
  for (let i = 0; i < nodes.length; i += 1) {
    const block = blockOf(nodes[i]!, nodes[i + 1], doc, refs)
    if (block === null) continue
    blocks.push(block)
    // Consume the heading that formed a section so it is not rendered twice.
    if (block.kind === 'section') i += 1
  }
  return blocks
}

function blockOf(
  node: SyntaxNode,
  next: SyntaxNode | undefined,
  doc: string,
  refs: Map<string, Ref>,
): Block | null {
  switch (node.name) {
    case 'Paragraph':
      return { kind: 'paragraph', inline: inlineOf(node, doc, refs) }
    case 'Blockquote':
      return { kind: 'quote', blocks: blocksOf(node, doc, refs) }
    case 'FencedCode':
    case 'CodeBlock': {
      // An unterminated fence (streaming) keeps CodeText to EOF — intended: show code early.
      const text = node.getChild('CodeText')
      const info = node.getChild('CodeInfo')
      const lang =
        info === null ? null : doc.slice(info.from, info.to).trim().split(/\s+/)[0] || null
      return { kind: 'code', lang, text: text === null ? '' : doc.slice(text.from, text.to) }
    }
    case 'BulletList':
    case 'OrderedList': {
      const ordered = node.name === 'OrderedList'
      const items = children(node)
        .filter((child) => child.name === 'ListItem')
        .map((item) => listItemOf(item, doc, refs))
      const mark = items.length > 0 ? children(node)[0]!.getChild('ListMark') : null
      const start =
        ordered && mark !== null
          ? Number.parseInt(doc.slice(mark.from, mark.to), 10) || 1
          : 1
      return { kind: 'list', ordered, start, items }
    }
    case 'Table':
      return tableOf(node, doc, refs)
    case 'HorizontalRule': {
      const level = next === undefined ? null : headingLevel(next.name)
      return level === null ? { kind: 'hr' } : { kind: 'section', level, inline: inlineOf(next!, doc, refs) }
    }
    case 'LinkReference':
      return null // definitions produce no content
    case 'Task': {
      // GFM task: content hangs under `Task`; test for content after the marker, not children.
      const marker = node.getChild('TaskMarker')
      const hasContent =
        children(node).some((child) => !BLOCK_SYNTAX.has(child.name)) ||
        node.to > (marker?.to ?? node.from)
      return hasContent ? { kind: 'paragraph', inline: inlineOf(node, doc, refs) } : null
    }
    default: {
      const level = headingLevel(node.name)
      if (level !== null) return { kind: 'heading', level, inline: inlineOf(node, doc, refs) }
      const text = doc.slice(node.from, node.to).trim()
      return text === '' ? null : { kind: 'literal', text }
    }
  }
}

function listItemOf(node: SyntaxNode, doc: string, refs: Map<string, Ref>): ListItem {
  const task = children(node).find((child) => child.name === 'Task')
  const marker = task?.getChild('TaskMarker')
  const checked =
    marker === null || marker === undefined
      ? null
      : doc.slice(marker.from, marker.to).toLowerCase().startsWith('[x]')
  return { checked, blocks: blocksOf(node, doc, refs) }
}

function tableOf(node: SyntaxNode, doc: string, refs: Map<string, Ref>): Block {
  const nodes = children(node)
  // Alignment comes from the delimiter row; header and body take TableCell nodes only.
  const delimiter = nodes.find(
    (child) => child.name === 'TableDelimiter' && doc.slice(child.from, child.to).includes('-'),
  )
  const align =
    delimiter === undefined
      ? []
      : doc
          .slice(delimiter.from, delimiter.to)
          .trim()
          .replace(/^\|/, '')
          .replace(/\|$/, '')
          .split('|')
          .map((cell): Align => {
            const text = cell.trim()
            if (text.startsWith(':') && text.endsWith(':')) return 'center'
            if (text.endsWith(':')) return 'right'
            return 'left'
          })
  const cells = (row: SyntaxNode) =>
    children(row)
      .filter((cell) => cell.name === 'TableCell')
      .map((cell) => inlineOf(cell, doc, refs))
  const head = nodes.find((child) => child.name === 'TableHeader')
  return {
    kind: 'table',
    align,
    head: head === undefined ? [] : cells(head),
    rows: nodes.filter((child) => child.name === 'TableRow').map(cells),
  }
}

// ---------------------------------------------------------------- inline level

function inlineOf(
  parent: SyntaxNode,
  doc: string,
  refs: Map<string, Ref>,
  skip: Set<string> = SYNTAX_NODES,
): Inline[] {
  const out: Inline[] = []
  let cursor = parent.from
  // The single space after a block-level marker is syntax; every other gap is content.
  let trimLeading = false
  const emit = (from: number, to: number) => {
    if (to <= from) return
    let text = doc.slice(from, to)
    if (trimLeading) {
      text = text.replace(/^[ \t]+/, '')
      trimLeading = false
    }
    out.push(...textOf(text))
  }
  for (const node of children(parent)) {
    if (skip.has(node.name)) {
      // Markers are skipped, but the gap before them is content: the text between two marks.
      emit(cursor, node.from)
      if (MARKER_TRIM.has(node.name)) trimLeading = true
      cursor = node.to
      continue
    }
    emit(cursor, node.from)
    out.push(...(inlineNode(node, doc, refs) ?? textOf(doc.slice(node.from, node.to))))
    cursor = node.to
  }
  emit(cursor, parent.to)
  return out
}

function inlineNode(node: SyntaxNode, doc: string, refs: Map<string, Ref>): Inline[] | null {
  switch (node.name) {
    case 'Emphasis':
      return [{ kind: 'em', children: inlineOf(node, doc, refs) }]
    case 'StrongEmphasis':
      return [{ kind: 'strong', children: inlineOf(node, doc, refs) }]
    case 'Strikethrough':
      return [{ kind: 'del', children: inlineOf(node, doc, refs) }]
    case 'InlineCode':
      return [{ kind: 'code', text: codeSpanText(doc.slice(node.from, node.to)) }]
    case 'HardBreak':
      return [{ kind: 'break' }]
    case 'Escape': {
      const raw = doc.slice(node.from, node.to)
      // Backslash + newline is a hard break (Lezer usually emits HardBreak; fallback here).
      return raw.endsWith('\n') ? [{ kind: 'break' }] : [{ kind: 'text', text: raw.slice(1) }]
    }
    case 'Entity': {
      const decoded = decodeEntity(doc.slice(node.from, node.to))
      return decoded === null ? null : [{ kind: 'text', text: decoded }]
    }
    case 'URL': {
      const text = doc.slice(node.from, node.to)
      const href = autolinkHref(text)
      return isSafeHref(href)
        ? [{ kind: 'link', href, children: [{ kind: 'text', text }] }]
        : [{ kind: 'text', text }]
    }
    case 'Autolink': {
      const url = node.getChild('URL')
      if (url === null) return null
      const text = doc.slice(url.from, url.to)
      // `<javascript:…>` is a valid CommonMark autolink too, so the scheme gate must apply.
      return isSafeHref(text)
        ? [{ kind: 'link', href: text, children: [{ kind: 'text', text }] }]
        : null
    }
    case 'Link': {
      const href = linkTarget(node, doc, refs)
      // Undefined reference or a scheme outside the allow-list: literal text, as CommonMark does.
      if (href === null || !isSafeHref(href)) return null
      return [{ kind: 'link', href, children: inlineOf(node, doc, refs, LINK_SYNTAX) }]
    }
    case 'Image': {
      const src = linkTarget(node, doc, refs)
      if (src === null || !isSafeImageSrc(src)) return null
      return [{ kind: 'image', src, alt: plainText(inlineOf(node, doc, refs, LINK_SYNTAX)) }]
    }
    default:
      return null
  }
}

/** Inside links/images the destination and title count as syntax too. */
const LINK_SYNTAX = new Set([...SYNTAX_NODES, ...DEST_NODES])

/** Target of a link/image: inline form uses URL, reference forms look up the definitions. */
function linkTarget(node: SyntaxNode, doc: string, refs: Map<string, Ref>): string | null {
  const url = node.getChild('URL')
  if (url !== null) return doc.slice(url.from, url.to)
  const label = node.getChild('LinkLabel')
  const raw = label === null ? '' : labelText(doc.slice(label.from, label.to))
  const fallback = plainText(inlineOf(node, doc, refs, LINK_SYNTAX))
  return refs.get(normalizeLabel(raw === '' ? fallback : raw))?.href ?? null
}

function plainText(inline: Inline[]): string {
  return inline
    .map((node) => {
      if (node.kind === 'text' || node.kind === 'code') return node.text
      if ('children' in node) return plainText(node.children)
      if (node.kind === 'image') return node.alt
      return ' '
    })
    .join('')
}

/** `\n` → soft break: newlines inside a paragraph are part of the content. */
function textOf(text: string): Inline[] {
  if (text === '') return []
  const out: Inline[] = []
  text.split('\n').forEach((part, index) => {
    if (index > 0) out.push({ kind: 'softbreak' })
    if (part !== '') out.push({ kind: 'text', text: part })
  })
  return out
}

/** Strips the backtick run around a code span. */
function codeSpanText(raw: string): string {
  const ticks = /^`+/.exec(raw)?.[0].length ?? 0
  const body = raw.slice(ticks, raw.length - ticks)
  // CommonMark: newlines fold to spaces; one leading/trailing space is stripped when both exist.
  const flat = body
    .split('\n')
    .map((line, index) => (index === 0 ? line : ` ${line.trimStart()}`))
    .join('')
  return /^ .+ $/.test(flat) ? flat.slice(1, -1) : flat
}

const NAMED_ENTITIES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: '\u00a0',
  copy: '©',
  reg: '®',
  trade: '™',
  hellip: '…',
  mdash: '—',
  ndash: '–',
  laquo: '«',
  raquo: '»',
  times: '×',
  divide: '÷',
  deg: '°',
  plusmn: '±',
  frac12: '½',
  sup2: '²',
  sup3: '³',
  middot: '·',
  bull: '•',
  larr: '←',
  rarr: '→',
  harr: '↔',
}

/** Numeric entities by code point, common named ones from the table; unknown → null (raw text). */
function decodeEntity(raw: string): string | null {
  const body = raw.replace(/^&/, '').replace(/;$/, '')
  if (/^#x/i.test(body)) {
    const code = Number.parseInt(body.slice(2), 16)
    return Number.isFinite(code) ? safeChar(code) : null
  }
  if (body.startsWith('#')) {
    const code = Number.parseInt(body.slice(1), 10)
    return Number.isFinite(code) ? safeChar(code) : null
  }
  return NAMED_ENTITIES[body.toLowerCase()] ?? null
}

function safeChar(code: number): string | null {
  if (!Number.isFinite(code) || code <= 0 || code > 0x10ffff) return null
  // A lone surrogate would throw in `fromCodePoint`; use the replacement char, as browsers do.
  if (code >= 0xd800 && code <= 0xdfff) return '\ufffd'
  return String.fromCodePoint(code)
}

/** GFM autolink href: `www.` gets http, an email gets mailto, an existing scheme is kept. */
function autolinkHref(text: string): string {
  if (/^[a-z][a-z0-9+.-]*:/i.test(text)) return text
  if (text.includes('@')) return `mailto:${text}`
  return `http://${text}`
}

/**
 * Link scheme gate: only http(s) / mailto / in-page anchors / relative paths pass.
 * Everything else — `javascript:`, `data:`, `vbscript:`, `file:`, protocol-relative
 * `//host` — stays plain text. Attribute values are the one place model output reaches
 * the DOM, and this is the only gate needed: tag injection cannot happen, since the
 * whole render is React elements.
 */
const SAFE_HREF = /^(https?:\/\/|mailto:|#)/i

function isSafeHref(href: string): boolean {
  const text = href.trim()
  if (text === '') return false
  if (text.startsWith('//')) return false // protocol-relative = off-site
  if (/^[a-z][a-z0-9+.-]*:/i.test(text)) return SAFE_HREF.test(text)
  return true // no scheme: treat as a relative path
}

/** Image src: `data:image` inlines, everything else passes the link gate (external → link). */
function isSafeImageSrc(src: string): boolean {
  return /^data:image\//i.test(src) || isSafeHref(src)
}
