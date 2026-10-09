/**
 * 设置 → 会话存储（阶段 56）：会话目录在哪、由谁决定、怎么改。
 *
 * 只改「新会话写哪」——**不搬已有会话**，所以保存后旧目录里的会话原地不动，
 * 要搬是 `avid session migrate` 的事（界面把这句话写在这儿，不然人会以为改个路径
 * 会话就跟过去了）。来源是环境变量 AVID_SESSIONS_DIR 时整段只读：它赢过配置文件，
 * 允许在这里写会写出一个「保存成功但不生效」的假象。
 *
 * 「选择文件夹」复用工作区那套系统选择器（POST /api/workspaces/pick，只回路径）；
 * 拿不到选择器时按钮给出下一步（手输路径），不是死路。
 */

import { useEffect, useState } from 'react'

import { getSessionsDir, pickFolder, setSessionsDir } from '../../api/client'
import type { SessionsDir } from '../../api/types'
import { Button } from '../../ui/Button'
import { cx } from '../../ui/cx'
import { Input } from '../../ui/Input'

const STATUS = 'mt-a8 font-ui text-hint'

const SOURCE_LABEL: Record<SessionsDir['source'], string> = {
  env: '环境变量 AVID_SESSIONS_DIR',
  settings: '设置文件 settings.json',
  default: '默认位置',
}

export function SessionsSection() {
  const [current, setCurrent] = useState<SessionsDir | null>(null)
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    getSessionsDir()
      .then((state) => {
        if (!alive) return
        setCurrent(state)
        setDraft(state.dir)
      })
      .catch((e: unknown) => {
        if (alive) setStatus({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
      })
    return () => {
      alive = false
    }
  }, [])

  const save = async (next: string) => {
    setBusy(true)
    try {
      const state = await setSessionsDir(next)
      setCurrent(state)
      setDraft(state.dir)
      setStatus({
        kind: 'ok',
        text: next.trim() ? '已改到新目录（已有会话留在原处）' : '已恢复默认位置',
      })
    } catch (e: unknown) {
      setStatus({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const choose = async () => {
    try {
      const picked = await pickFolder()
      if (picked.path) setDraft(picked.path)
    } catch (e: unknown) {
      setStatus({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    }
  }

  const editable = current?.editable ?? false
  const changed = current !== null && draft.trim() !== current.dir

  return (
    <div className="mt-a8 rounded-md border-hairline border-hair bg-card p-a12">
      <div className="flex items-baseline gap-a6">
        <span className="font-ui text-hint text-ink-muted">当前目录</span>
        {current && (
          <span className="font-ui text-micro text-ink-muted">
            来源：{SOURCE_LABEL[current.source]}
          </span>
        )}
      </div>
      <p className="mt-a4 break-all font-mono text-hint text-ink">
        {current ? current.dir : '（读取中…）'}
      </p>

      <div className="mt-a8 flex items-center gap-a8">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={!editable || busy}
          placeholder="绝对路径，例如 /data/avid-sessions"
          aria-label="会话目录"
        />
        <Button onClick={choose} disabled={!editable || busy}>
          选择文件夹
        </Button>
      </div>

      <div className="mt-a8 flex items-center gap-a8">
        <Button onClick={() => void save(draft)} disabled={!editable || busy || !changed}>
          保存位置
        </Button>
        <Button
          onClick={() => void save('')}
          disabled={!editable || busy || current?.source !== 'settings'}
        >
          恢复默认
        </Button>
        {current && current.source !== 'default' && (
          <span className="font-ui text-micro text-ink-muted">默认：{current.default_dir}</span>
        )}
      </div>

      <p className="mt-a8 font-ui text-micro leading-[1.6] text-ink-muted">
        {editable
          ? '改位置不搬会话：新会话写新目录，已有会话留在原处（要搬用 avid session migrate）。'
          : 'AVID_SESSIONS_DIR 已经定了这个位置，改这里不会生效；去掉那个环境变量再改。'}
      </p>
      {status && (
        <p className={cx(STATUS, status.kind === 'error' ? 'text-danger' : 'text-ink-light')}>
          {status.text}
        </p>
      )}
    </div>
  )
}
