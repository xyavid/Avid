/**
 * markdown 块级解析（阶段 33 · 阶段 9）——纯函数，不碰 DOM。
 *
 * 只做模型输出里真实会出现的那些块：围栏代码、标题、分隔线、引用、有序/无序列表
 * （含一层以上嵌套）、GFM 表格、段落。**不追求 CommonMark 全量**：没覆盖的写法按
 * 段落原样显示，不会丢内容、不会变形。
 *
 * 两条为「对话」这个场景专门定的事：
 *   1. **半截输入要成块**：流式过程中围栏还没收尾，也必须按代码块渲染（否则每来
 *      一个 delta 都在正文与代码之间跳）；
 *   2. **软换行保留**：段落里的换行原样带下去（渲染成 <br>）。模型排版里的换行是
 *      它组织内容的一部分；中文之间补空格比保留换行更糟。
 *
 * 一个解析期的合并：**分隔线紧跟标题**时合成 `section`（带线小节标题）。参考界面里
 * 「一条线 + 居中标题」是**一节的开头**，不是「所有小节标题都长这样」——把它写成
 * 按层级固定居中会很死板（模型随手写个二级标题也居中，读起来到处是断点）。
 * 让原文决定：写了线才起一节，没写就还是普通标题。
 */

export type Align = 'left' | 'center' | 'right'

/** 标题层级（1–6）。 */
export type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6

export type ListItem = {
  /** 项自身的文本（多行用 \n 连），渲染时走行内解析 */
  text: string
  /** 缩进更深的后续块（嵌套列表等） */
  children: Block[]
}

export type Block =
  | { kind: 'paragraph'; text: string }
  /** 带线小节标题：原本写的是「分隔线 + 标题」，合成一块，渲染成线在上、标题居中。 */
  | { kind: 'section'; level: HeadingLevel; text: string }
  | { kind: 'heading'; level: HeadingLevel; text: string }
  | { kind: 'code'; lang: string | null; text: string }
  | { kind: 'list'; ordered: boolean; start: number; items: ListItem[] }
  | { kind: 'quote'; blocks: Block[] }
  | { kind: 'table'; head: string[]; align: Align[]; rows: string[][] }
  | { kind: 'hr' }

const FENCE = /^(\s{0,3})(`{3,}|~{3,})\s*([^\s`]*)/
const HEADING = /^(\s{0,3})(#{1,6})\s+(.*)$/
const HR = /^\s{0,3}((?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$/
const BULLET = /^(\s*)([-*+])\s+(.*)$/
const ORDERED = /^(\s*)(\d{1,9})[.)]\s+(.*)$/
const QUOTE = /^\s{0,3}>\s?(.*)$/
const TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/

function isBlank(line: string): boolean {
  return line.trim() === ''
}

/** 表格分隔行 → 每列对齐；不是分隔行返回 null。 */
function parseAlign(row: string): Align[] | null {
  if (!TABLE_SEP.test(row) || !row.includes('-')) return null
  const cells = splitRow(row)
  return cells.map((c) => {
    const left = c.startsWith(':')
    const right = c.endsWith(':')
    if (left && right) return 'center'
    if (right) return 'right'
    return 'left'
  })
}

function splitRow(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim())
}

/** 一段「列表行」的缩进宽度（制表符按 4 计）。 */
function indentOf(line: string): number {
  const m = /^(\s*)/.exec(line)
  const raw = m?.[1] ?? ''
  return raw.replace(/\t/g, '    ').length
}

export function parseBlocks(src: string): Block[] {
  return mergeSections(parseRange(src.split('\n')))
}

/** 分隔线紧跟标题 → 合成一块（见文件头：一节的开头由原文的线决定，不按层级写死）。 */
function mergeSections(blocks: Block[]): Block[] {
  const out: Block[] = []
  for (let i = 0; i < blocks.length; i++) {
    const cur = blocks[i]
    const next = blocks[i + 1]
    if (cur?.kind === 'hr' && next?.kind === 'heading') {
      out.push({ kind: 'section', level: next.level, text: next.text })
      i += 1
      continue
    }
    if (cur) out.push(cur)
  }
  return out
}

function parseRange(lines: string[]): Block[] {
  const blocks: Block[] = []
  let i = 0
  let para: string[] = []

  const flushPara = () => {
    if (para.length > 0) {
      blocks.push({ kind: 'paragraph', text: para.join('\n') })
      para = []
    }
  }

  while (i < lines.length) {
    const line = lines[i] ?? ''

    if (isBlank(line)) {
      flushPara()
      i++
      continue
    }

    const fence = FENCE.exec(line)
    if (fence) {
      flushPara()
      const marker = fence[2] ?? '```'
      const lang = (fence[3] ?? '').trim() || null
      const body: string[] = []
      i++
      // 收尾围栏：同种字符且不短于起始；找不到就当流式截断，吃到文末
      const closing = new RegExp(`^\\s{0,3}${marker[0] === '`' ? '`' : '~'}{${marker.length},}\\s*$`)
      while (i < lines.length && !closing.test(lines[i] ?? '')) {
        body.push(lines[i] ?? '')
        i++
      }
      if (i < lines.length) i++ // 跳掉收尾行
      blocks.push({ kind: 'code', lang, text: body.join('\n') })
      continue
    }

    const heading = HEADING.exec(line)
    if (heading) {
      flushPara()
      blocks.push({ kind: 'heading', level: (heading[2] ?? '#').length as HeadingLevel, text: heading[3] ?? '' })
      i++
      continue
    }

    if (HR.test(line)) {
      flushPara()
      blocks.push({ kind: 'hr' })
      i++
      continue
    }

    if (QUOTE.test(line)) {
      flushPara()
      const inner: string[] = []
      while (i < lines.length) {
        const cur = lines[i] ?? ''
        const m = QUOTE.exec(cur)
        if (m) {
          inner.push(m[1] ?? '')
          i++
        } else if (isBlank(cur)) {
          inner.push('')
          i++
        } else {
          break
        }
      }
      blocks.push({ kind: 'quote', blocks: parseRange(inner) })
      continue
    }

    // 表格：本行有 |，下一行是分隔行
    if (line.includes('|') && i + 1 < lines.length) {
      const align = parseAlign(lines[i + 1] ?? '')
      if (align) {
        flushPara()
        const head = splitRow(line)
        const rows: string[][] = []
        i += 2
        while (i < lines.length && (lines[i] ?? '').includes('|') && !isBlank(lines[i] ?? '')) {
          rows.push(splitRow(lines[i] ?? ''))
          i++
        }
        blocks.push({ kind: 'table', head, align, rows })
        continue
      }
    }

    if (BULLET.test(line) || ORDERED.test(line)) {
      flushPara()
      const base = indentOf(line)
      const ordered = ORDERED.test(line)
      const start = ordered ? Number(ORDERED.exec(line)?.[2] ?? '1') : 1
      const items: ListItem[] = []
      while (i < lines.length) {
        const cur = lines[i] ?? ''
        if (isBlank(cur)) break
        const m = (ordered ? ORDERED.exec(cur) : BULLET.exec(cur)) ?? null
        if (m && indentOf(cur) === base) {
          items.push({ text: m[3] ?? '', children: [] })
          i++
          continue
        }
        // 非标记行：缩进更深 → 归到当前项（可能是嵌套列表），否则列表结束
        if (items.length > 0 && indentOf(cur) > base) {
          const rest: string[] = []
          while (i < lines.length) {
            const l = lines[i] ?? ''
            if (isBlank(l) || indentOf(l) <= base) break
            rest.push(l.slice(Math.min(base + 2, l.length)))
            i++
          }
          const nested = parseRange(rest)
          const current = items[items.length - 1]
          if (current) {
            for (const b of nested) {
              if (b.kind === 'paragraph' && current.children.length === 0 && b.text.trim() !== '' && !b.text.includes('\n\n')) {
                // 项自身的续行：并进 text，不另起段落
                current.text = current.text === '' ? b.text : `${current.text}\n${b.text}`
              } else {
                current.children.push(b)
              }
            }
          }
          continue
        }
        break
      }
      blocks.push({ kind: 'list', ordered, start, items })
      continue
    }

    para.push(line)
    i++
  }

  flushPara()
  return blocks
}
