/**
 * 浏览器面板（阶段 49）：地址栏 + iframe 内嵌 + 「新窗口打开」兜底。
 *
 * 物理限制如实写在空态里：多数外站以 X-Frame-Options / CSP frame-ancestors
 * 拒绝被内嵌（白屏），那不是实现缺陷——localhost 与开发服务器体验完整。
 * URL 只接受 http/https；sandbox 给标准四权（远端内容不进本站源）。
 */

import { useState } from 'react'

/** 补全协议并校验：只接受 http/https（javascript: 之类的输入直接拒）。 */
function normalize(raw: string): string | null {
  const text = raw.trim()
  if (!text) return null
  const withScheme = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(text) ? text : `https://${text}`
  try {
    const url = new URL(withScheme)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

export default function BrowserPanel() {
  const [draft, setDraft] = useState('')
  const [url, setUrl] = useState<string | null>(null)
  const [invalid, setInvalid] = useState(false)

  const go = () => {
    const next = normalize(draft)
    if (next === null) {
      setInvalid(true)
      return
    }
    setInvalid(false)
    setUrl(next)
  }

  return (
    <div className="flex h-full flex-col gap-a8">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          go()
        }}
        className="flex items-center gap-a4"
      >
        <input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            setInvalid(false)
          }}
          placeholder="输入 URL，回车打开"
          aria-label="浏览器地址"
          className="h-control min-w-0 flex-1 rounded-sm border-hairline border-hair bg-card px-[11px] font-ui text-ui text-ink placeholder:text-ink-muted focus:border-accent focus:shadow-focus-ring focus:outline-none"
        />
        <button
          type="submit"
          aria-label="打开"
          className="inline-flex h-[26px] shrink-0 items-center rounded-sm bg-accent px-a10 font-ui text-caption text-card transition-colors duration-fast ease-out hover:bg-accent-hover"
        >
          打开
        </button>
      </form>
      {invalid && <p className="font-ui text-micro text-danger">地址不合法（只支持 http/https）</p>}
      {url === null ? (
        <p className="font-ui text-hint text-ink-muted">
          输入 URL 开始浏览；多数外站拒绝被内嵌，白屏时用「新窗口打开」。
        </p>
      ) : (
        <>
          <div className="flex items-center justify-between gap-a8 font-ui text-micro text-ink-muted">
            <span className="min-w-0 truncate">{url}</span>
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="ml-a8 flex shrink-0 items-center gap-a4 text-accent hover:underline"
            >
              新窗口打开
            </a>
          </div>
          <iframe
            key={url}
            src={url}
            title="浏览器面板"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
            className="min-h-[240px] w-full flex-1 rounded-sm border-hairline border-hair bg-white"
          />
        </>
      )}
    </div>
  )
}
