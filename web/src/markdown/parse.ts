/**
 * markdown 解析：语法交给 CommonMark/GFM 的标准实现（`@lezer/markdown`，CodeMirror
 * 用的那个增量解析器），本模块只做「语法树 → 渲染模型」的翻译，不自己认语法。
 *
 * 为什么换成标准实现：自研解析器明说「不追求 CommonMark 全量」——嵌套列表、惰性续行、
 * setext 标题、缩进代码块、引用链接、转义与实体这些要么缺、要么各写各的；模型输出的
 * 写法千变万化，把规格交给规格实现是唯一能收敛的路。
 *
 * 翻译约定（每条都是「与标准对齐」或「有意偏离」的显式记录）：
 *   1. **未识别的节点按原文当纯文本**：HTML 块与行内标签都走这条——不注入、不执行，
 *      将来 Lezer 新增的语法也不会丢内容；
 *   2. **引用链接自己解析**：Lezer 为了单遍增量解析**不校验引用定义**（README 明说），
 *      这里补上——查不到定义的 `[a][b]` 按字面文本渲染，与 CommonMark 一致；
 *   3. **软换行渲染成断行**（有意偏离）：对话场景的既定选择，模型排版里的换行是内容
 *      的一部分；硬换行（行尾两空格 / 反斜杠）本就该断行；
 *   4. **实体只解数值与常用名**：完整 HTML5 实体表要 15 kB gzip，聊天正文不值这个价；
 *      表外的写法按原样显示，不猜。
 *
 * 一个解析期的合并（阶段 33 既定视觉规则）：**分隔线紧跟标题**时合成 `section`
 * （带线小节标题）。参考界面里「一条线 + 居中标题」是**一节的开头**，不是「所有
 * 小节标题都长这样」——让原文决定：写了线才起一节，没写就还是普通标题。
 */

import type { SyntaxNode } from '@lezer/common'
import { GFM, parser } from '@lezer/markdown'

const md = parser.configure([GFM])

export type Align = 'left' | 'center' | 'right'

/** 标题层级（1–6）。 */
export type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'strong'; children: Inline[] }
  | { kind: 'em'; children: Inline[] }
  | { kind: 'del'; children: Inline[] }
  | { kind: 'link'; href: string; children: Inline[] }
  /** 图片：`src` 是 data: 地址时渲染成图，外链由渲染层降级成链接（CSP 只允许 self/data）。 */
  | { kind: 'image'; src: string; alt: string }
  /** 硬换行（行尾两空格 / 反斜杠结尾）。 */
  | { kind: 'break' }
  /** 软换行；渲染成断行是有意偏离，见模块注释约定 3。 */
  | { kind: 'softbreak' }

export type ListItem = {
  /** GFM 任务项的状态；null = 普通列表项。 */
  checked: boolean | null
  blocks: Block[]
}

export type Block =
  | { kind: 'paragraph'; inline: Inline[] }
  | { kind: 'heading'; level: HeadingLevel; inline: Inline[] }
  /** 带线小节标题：原文写的是「分隔线 + 标题」，合成一块，渲染成线在上、标题居中。 */
  | { kind: 'section'; level: HeadingLevel; inline: Inline[] }
  | { kind: 'code'; lang: string | null; text: string }
  | { kind: 'list'; ordered: boolean; start: number; items: ListItem[] }
  | { kind: 'quote'; blocks: Block[] }
  | { kind: 'table'; align: Align[]; head: Inline[][]; rows: Inline[][][] }
  | { kind: 'hr' }
  /** 未识别的块（HTML 块等）：原样当纯文本显示。 */
  | { kind: 'literal'; text: string }

type Ref = { href: string; title: string | null }

/** 行内语法标记：不是内容，跳过时也不能把它们的原文当文本吐出去。 */
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

/** 块级的语法标记：列表符号、引用尖括号、任务框——都不是内容。 */
const BLOCK_SYNTAX = new Set(['ListMark', 'TaskMarker', 'QuoteMark', 'CodeMark'])

/** 只在链接/图片内部才是语法的节点：裸 URL 在外面是 GFM 自动链接，要在外面认。 */
const DEST_NODES = new Set(['URL', 'LinkTitle'])

/** 这些标记后面紧跟的那一个空格是语法（`## 标题`、`- [ ] 待办`），不是内容。 */
const MARKER_TRIM = new Set(['HeaderMark', 'TaskMarker', 'ListMark'])

export function parseBlocks(text: string): Block[] {
  const tree = md.parse(text)
  const refs = collectRefs(tree.topNode, text)
  return blocksOf(tree.topNode, text, refs)
}

/** 引用定义（`[label]: url "title"`）整篇生效，所以先扫一遍；定义本身不产出内容。 */
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

/** CommonMark 的标签匹配：折叠空白、忽略大小写。 */
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

// ---------------------------------------------------------------- 块级

function blocksOf(parent: SyntaxNode, doc: string, refs: Map<string, Ref>): Block[] {
  const nodes = children(parent).filter((child) => !BLOCK_SYNTAX.has(child.name))
  const blocks: Block[] = []
  for (let i = 0; i < nodes.length; i += 1) {
    const block = blockOf(nodes[i]!, nodes[i + 1], doc, refs)
    if (block === null) continue
    blocks.push(block)
    // 合成一节时把那个标题也吃掉，别让它再渲染一次。
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
      // 围栏没写完（流式半截）时 CodeText 一直到文末——正是要的：先按代码块出现。
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
      return null // 定义本身不产出内容
    case 'Task': {
      // 单段的 GFM 任务项：内容直接挂在 Task 下（多段时 Task 只剩 TaskMarker，
      // 其余块是 ListItem 的同级兄弟）。判据是「标记之后还有内容」，不是「有子节点」——
      // `- [x] 完了` 里的「完了」是空隙，没有子节点。
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
  // 对齐来自分隔行（`|:--|--:|`）；表头与数据行都只取 TableCell。
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

// ---------------------------------------------------------------- 行内

function inlineOf(
  parent: SyntaxNode,
  doc: string,
  refs: Map<string, Ref>,
  skip: Set<string> = SYNTAX_NODES,
): Inline[] {
  const out: Inline[] = []
  let cursor = parent.from
  // 块级标记之后那一个空格属于语法（`## 标题` / `- [ ] 待办`），其余空隙都是内容。
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
      // 标记与目标跳过，但**它前面的空隙是内容**：`[甲][ref]` 的「甲」在两个方括号
      // 之间，`**注**` 的「注」在两个强调标记之间，`![图 **注**]` 的 alt 同理。
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
      // 反斜杠后是换行 = 硬换行（Lezer 一般已归 HardBreak，这里兜底）。
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
      // `<javascript:…>` 这类也在 CommonMark 的自动链接规则里，协议必须过闸。
      return isSafeHref(text)
        ? [{ kind: 'link', href: text, children: [{ kind: 'text', text }] }]
        : null
    }
    case 'Link': {
      const href = linkTarget(node, doc, refs)
      // 引用未定义、或协议不在放行名单：按字面文本渲染（前者与 CommonMark 一致）。
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

/** 链接/图片内部：目标与标题也算语法。 */
const LINK_SYNTAX = new Set([...SYNTAX_NODES, ...DEST_NODES])

/** 链接与图片的目标：行内形式取 URL，引用形式查定义（含 `[t][]` 与 `[t]`）。 */
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

/** `\n` → 软换行：段落里的换行是内容的一部分（模块注释约定 3）。 */
function textOf(text: string): Inline[] {
  if (text === '') return []
  const out: Inline[] = []
  text.split('\n').forEach((part, index) => {
    if (index > 0) out.push({ kind: 'softbreak' })
    if (part !== '') out.push({ kind: 'text', text: part })
  })
  return out
}

/** 代码段两端那一圈反引号：`` ``a`b`` `` → ``a`b``。 */
function codeSpanText(raw: string): string {
  const ticks = /^`+/.exec(raw)?.[0].length ?? 0
  const body = raw.slice(ticks, raw.length - ticks)
  // CommonMark：代码段里的换行折成空格；两端各挂一个空格时各去掉一个。
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

/** 数值实体按算法解，常用名查表；表外返回 null，调用方按原文显示。 */
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
  // 落单的代理码点直接 fromCodePoint 会抛异常，按替换字符处理（与浏览器一致）。
  if (code >= 0xd800 && code <= 0xdfff) return '\ufffd'
  return String.fromCodePoint(code)
}

/** GFM 自动链接的目标：www. 补 http，邮箱补 mailto，带协议的照用。 */
function autolinkHref(text: string): string {
  if (/^[a-z][a-z0-9+.-]*:/i.test(text)) return text
  if (text.includes('@')) return `mailto:${text}`
  return `http://${text}`
}

/**
 * 链接协议闸门：只放行 http(s) / mailto / 页内锚点 / 相对路径。
 *
 * 其余（`javascript:` / `data:` / `vbscript:` / `file:` …）与协议相对的 `//host`
 * 一律不认，按纯文本留着——模型输出是外部内容，这是进 DOM 的属性值里唯一需要设防的
 * 一处（`<img onerror>` 那种标签注入在解析层就不可能发生：全程 React 元素）。
 */
const SAFE_HREF = /^(https?:\/\/|mailto:|#)/i

function isSafeHref(href: string): boolean {
  const text = href.trim()
  if (text === '') return false
  if (text.startsWith('//')) return false // 协议相对 = 站外
  if (/^[a-z][a-z0-9+.-]*:/i.test(text)) return SAFE_HREF.test(text)
  return true // 没有协议：按相对路径放行
}

/** 图片的地址：data:image 直接内嵌，其余按链接闸门（外链图渲染层降级成链接）。 */
function isSafeImageSrc(src: string): boolean {
  return /^data:image\//i.test(src) || isSafeHref(src)
}
