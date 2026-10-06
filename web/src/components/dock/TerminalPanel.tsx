/**
 * 终端面板（阶段 49）：WebSocket + xterm.js，一条连接一个独立交互 shell。
 *
 * - cwd 固定为当前会话的工作区根（由页面下发）；断开（关面板/换会话/关 dock）
 *   即杀整个进程组——生命周期跟连接走，不留孤儿 shell。
 * - xterm 按需加载：dynamic import 把它的体积隔离进异步 chunk（体积门禁分档计量）。
 * - Windows 后端会回 error frame（标准库无 PTY），这里照实显示，不做伪装。
 * - 主题只取运行时计算色（前景）与透明底：终端底色跟随纸面 token，不另立一套。
 */

import { useEffect, useRef, useState } from 'react'

type WsFrame = { type: 'out' | 'exit' | 'error'; data?: string; code?: number; message?: string }

type TerminalStatus = 'idle' | 'connecting' | 'connected' | 'closed' | 'error'

const STATUS_LABEL: Record<TerminalStatus, string> = {
  idle: '未选择工作区',
  connecting: '连接中…',
  connected: '已连接（输入 exit 结束）',
  closed: '已断开',
  error: '出错',
}

export default function TerminalPanel({ root }: { root: string | null }) {
  const holder = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<TerminalStatus>('idle')
  const [message, setMessage] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (root === null) {
      setStatus('idle')
      setMessage(null)
      return
    }
    let disposed = false
    const cleanups: (() => void)[] = []
    setStatus('connecting')
    setMessage(null)
    void (async () => {
      try {
        const [{ Terminal }, { FitAddon }] = await Promise.all([
          import('@xterm/xterm'),
          import('@xterm/addon-fit'),
        ])
        if (disposed || holder.current === null) return
        const holderEl = holder.current
        const style = window.getComputedStyle(holderEl)
        const term = new Terminal({
          fontFamily: style.fontFamily || 'monospace',
          fontSize: 12,
          cursorBlink: true,
          theme: { background: 'rgba(0,0,0,0)', foreground: style.color },
        })
        const fit = new FitAddon()
        term.loadAddon(fit)
        term.open(holderEl)
        fit.fit()

        const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
        const ws = new WebSocket(
          `${proto}://${window.location.host}/api/ws/terminal?root=${encodeURIComponent(root)}&cols=${term.cols}&rows=${term.rows}`,
        )
        term.onData((data) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'in', data }))
        })
        const onResize = () => {
          fit.fit()
          if (ws.readyState === WebSocket.OPEN)
            ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
        }
        const observer = new ResizeObserver(onResize)
        observer.observe(holderEl)
        ws.onopen = () => {
          if (!disposed) setStatus('connected')
        }
        ws.onmessage = (event) => {
          const frame = JSON.parse(event.data) as WsFrame
          if (frame.type === 'out') {
            term.write(frame.data ?? '')
          } else if (frame.type === 'exit') {
            setStatus('closed')
            setMessage(`进程已退出（码 ${frame.code ?? 0}）——点重连再起一个 shell`)
          } else if (frame.type === 'error') {
            setStatus('error')
            setMessage(frame.message ?? '终端错误')
          }
        }
        ws.onclose = () => {
          if (!disposed) {
            setStatus((cur) => (cur === 'error' || cur === 'closed' ? cur : 'closed'))
            setMessage((cur) => cur ?? '连接已断开')
          }
        }
        cleanups.push(() => {
          observer.disconnect()
          ws.close()
          term.dispose()
        })
      } catch (err) {
        if (!disposed) {
          setStatus('error')
          setMessage(err instanceof Error ? err.message : String(err))
        }
      }
    })()
    return () => {
      disposed = true
      for (const fn of cleanups) fn()
    }
  }, [root, attempt])

  return (
    <div className="flex h-full flex-col gap-a8">
      <div className="flex items-center justify-between font-ui text-caption text-ink-muted">
        <span>{STATUS_LABEL[status]}</span>
        {(status === 'closed' || status === 'error') && (
          <button
            type="button"
            onClick={() => setAttempt((n) => n + 1)}
            className="rounded-sm border-hairline border-hair px-a8 py-[2px] text-accent transition-colors duration-fast ease-out hover:bg-overlay-light"
          >
            重连
          </button>
        )}
      </div>
      {message && <p className="font-ui text-hint text-ink-muted">{message}</p>}
      <div
        ref={holder}
        className="min-h-[200px] flex-1 overflow-hidden rounded-sm border-hairline border-hair bg-overlay-subtle p-a4"
      />
    </div>
  )
}
