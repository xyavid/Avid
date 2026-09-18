/**
 * 工具结果与链接的统一入口：不管后端给的是字符串、对象、多模态 parts 还是错误，
 * 都收敛成一段可以丢进 `whitespace-pre-wrap` 的纯文本；链接只放行能安全打开的目标。
 *
 * 不在这里做 HTML 转义：调用方一律把结果当文本节点渲染，转义发生在 React 里，
 * 这里再转一次只会把 `&lt;` 显示成字面量。
 */

import { DEFAULT_LOCALE, translate } from '../i18n'

function stringify(value: unknown): string {
  try {
    const text = JSON.stringify(value, null, 2)
    return text === undefined ? String(value) : text
  } catch {
    return String(value)
  }
}

function errorText(error: unknown): string {
  if (typeof error === 'string') return error
  if (error && typeof error === 'object') {
    const record = error as Record<string, unknown>
    if (typeof record.message === 'string') return record.message
    if (typeof record.detail === 'string') return record.detail
    return stringify(record)
  }
  return typeof error === 'number' || typeof error === 'boolean' ? String(error) : ''
}

function fromParts(parts: unknown[]): string {
  const chunks: string[] = []
  for (const part of parts) {
    if (typeof part === 'string') {
      chunks.push(part)
      continue
    }
    if (!part || typeof part !== 'object') continue
    const record = part as Record<string, unknown>
    if (typeof record.text === 'string') {
      chunks.push(record.text)
      continue
    }
    if (typeof record.type === 'string' && record.type !== 'text') {
      chunks.push(translate(DEFAULT_LOCALE, 'tools.attachment', { type: record.type }))
    }
  }
  return chunks.join('\n')
}

export function sanitizeToolContent(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return fromParts(value)
  if (typeof value !== 'object') return String(value)

  const record = value as Record<string, unknown>
  if (record.error !== undefined && record.error !== null && record.error !== '') {
    const text = errorText(record.error)
    if (text) return text
  }
  if (record.content !== undefined) {
    const text = sanitizeToolContent(record.content)
    if (text) return text
    // 内容为空时 metadata 往往是唯一事实（例如「0 条匹配」这类结构化结果）。
    if (record.metadata !== undefined) return stringify(record.metadata)
    return ''
  }
  if (typeof record.text === 'string') return record.text
  return stringify(record)
}

const SCHEME = /^[a-z][a-z0-9+.-]*:/i

/**
 * 链接白名单：`http(s)://` 与工作区相对路径（含锚点）放行，其余一律拒绝。
 * `javascript:` / `data:` 这类伪协议一旦落到 href 上就是一次注入，所以在源头掐掉。
 */
export function sanitizeUrl(url: string): string | null {
  const value = url.trim()
  if (!value) return null
  // 协议相对地址（`//host/x`，以及 `\\host\x` 这种浏览器会当斜杠处理的写法）
  // 会直接打到外部主机：模型输出里的 `![](//attacker/x)` 渲染即出网。先掐掉。
  if (/^[\\/]{2}/.test(value)) return null
  if (SCHEME.test(value)) return /^https?:\/\//i.test(value) ? value : null
  return value
}
