/**
 * ANSI SGR → 带色调的片段：只认前景色、粗体与复位，其余转义序列剥掉不显示。
 *
 * 不引 xterm：光标、清屏、回滚是另一个量级的需求，而工具输出里真正出现的只有
 * 颜色和粗体。剥掉而非原样保留，是因为转义序列在等宽区里会变成一串乱码。
 */

export type AnsiTone = 'red' | 'green' | 'yellow' | 'blue' | 'purple' | 'fg'

export interface AnsiSpan {
  text: string
  tone?: AnsiTone
  bold?: boolean
}

/** 30–37 / 90–97 前景；青（36/96）与黑/白（30/37/90/97）都归到已定义的色调。 */
const TONES: Record<number, AnsiTone> = {
  30: 'fg',
  31: 'red',
  32: 'green',
  33: 'yellow',
  34: 'blue',
  35: 'purple',
  36: 'blue',
  37: 'fg',
  90: 'fg',
  91: 'red',
  92: 'green',
  93: 'yellow',
  94: 'blue',
  95: 'purple',
  96: 'blue',
  97: 'fg',
}

// 第一条分支是唯一保留语义的（SGR）；其余 CSI / OSC / 双字符转义一律丢弃。
const ESCAPE_SOURCE = [
  '\u001b\\[([0-9;]*)m',
  '\u001b\\[[0-9;?]*[ -/]*[@-~]',
  '\u001b\\][^\u0007]*(?:\u0007|\u001b\\\\)',
  '\u001b[@-Z\\\\-_]',
].join('|')

export function renderAnsi(text: string): AnsiSpan[] {
  if (!text) return []
  const pattern = new RegExp(ESCAPE_SOURCE, 'g')
  const spans: AnsiSpan[] = []
  let tone: AnsiTone | undefined
  let bold = false
  let cursor = 0

  const push = (chunk: string): void => {
    if (!chunk) return
    const span: AnsiSpan = { text: chunk }
    if (tone) span.tone = tone
    if (bold) span.bold = true
    spans.push(span)
  }

  const apply = (params: string): void => {
    for (const raw of params.split(';')) {
      const code = raw === '' ? 0 : Number(raw)
      if (code === 0) {
        tone = undefined
        bold = false
      } else if (code === 1) {
        bold = true
      } else if (code === 22) {
        bold = false
      } else if (code === 39) {
        tone = undefined
      } else {
        const mapped = TONES[code]
        if (mapped) tone = mapped
      }
    }
  }

  for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
    push(text.slice(cursor, match.index))
    cursor = match.index + match[0].length
    const params = match[1]
    if (params !== undefined) apply(params)
  }
  push(text.slice(cursor))
  return spans
}
