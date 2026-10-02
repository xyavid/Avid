/**
 * markdown 行内解析（阶段 33 · 阶段 9）——纯函数，产出**节点**而不是 HTML 字符串。
 *
 * 这是安全边界所在：模型输出直接进 DOM，所以这里从设计上就不可能注入标签——
 * 原文里的 `<img onerror=…>` 只会变成一段文本节点。
 *
 * 支持的写法：行内代码、粗体（** / __）、斜体（* / _）、删除线（~~）、链接
 * （只放行 http/https/mailto 与页内锚点）、反斜杠转义。
 * 没闭合的标记按字面返回——流式过程中每一帧都可能处于「刚打了一半」的状态。
 */

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'strong'; children: Inline[] }
  | { kind: 'em'; children: Inline[] }
  | { kind: 'del'; children: Inline[] }
  | { kind: 'link'; href: string; children: Inline[] }

/** 只放行这几种协议；其余（javascript: / data: / vbscript: …）按纯文本留着。 */
const SAFE_HREF = /^(https?:\/\/|mailto:|#)/i

export function parseInline(src: string): Inline[] {
  const out: Inline[] = []
  let text = ''
  let i = 0

  const flush = () => {
    if (text !== '') {
      out.push({ kind: 'text', text })
      text = ''
    }
  }

  while (i < src.length) {
    const ch = src[i] ?? ''

    // 反斜杠转义
    if (ch === '\\' && i + 1 < src.length) {
      text += src[i + 1] ?? ''
      i += 2
      continue
    }

    // 行内代码
    if (ch === '`') {
      const close = src.indexOf('`', i + 1)
      if (close > i) {
        flush()
        out.push({ kind: 'code', text: src.slice(i + 1, close) })
        i = close + 1
        continue
      }
    }

    // 链接：[文本](地址)
    if (ch === '[') {
      const mid = src.indexOf('](', i + 1)
      if (mid > i) {
        const close = src.indexOf(')', mid + 2)
        if (close > mid) {
          const href = src.slice(mid + 2, close).trim()
          if (SAFE_HREF.test(href)) {
            flush()
            out.push({ kind: 'link', href, children: parseInline(src.slice(i + 1, mid)) })
            i = close + 1
            continue
          }
        }
      }
    }

    // 删除线 / 粗体 / 斜体：找配对的同种标记，找不到就当字面
    const strong = matchDelimited(src, i, '**') ?? matchDelimited(src, i, '__')
    if (strong) {
      flush()
      out.push({ kind: 'strong', children: parseInline(strong.inner) })
      i = strong.end
      continue
    }
    const del = matchDelimited(src, i, '~~')
    if (del) {
      flush()
      out.push({ kind: 'del', children: parseInline(del.inner) })
      i = del.end
      continue
    }
    const em = matchDelimited(src, i, '*') ?? matchDelimited(src, i, '_')
    if (em) {
      flush()
      out.push({ kind: 'em', children: parseInline(em.inner) })
      i = em.end
      continue
    }

    text += ch
    i++
  }

  flush()
  return out
}

/** 从 i 处尝试匹配成对标记；成功返回内容与结束下标，失败返回 null。 */
function matchDelimited(src: string, i: number, marker: string): { inner: string; end: number } | null {
  if (!src.startsWith(marker, i)) return null
  const start = i + marker.length
  if (src.slice(start).startsWith(marker)) return null // 空内容不算（**** 这种）
  const close = src.indexOf(marker, start)
  if (close < 0) return null
  const inner = src.slice(start, close)
  if (inner.trim() === '') return null
  return { inner, end: close + marker.length }
}
