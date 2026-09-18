import { useEffect, useRef } from 'react'

import { Button, TextArea } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useUiStore } from '../../../state/uiStore'
import type { PermissionMode } from '../../../api/types'
import { PermissionSelector } from './PermissionSelector'

export interface ComposerProps {
  busy: boolean
  canSend: boolean
  stopping?: boolean
  /** 这次运行用哪一档权限（route 已按工作区默认回落过）。 */
  permission: PermissionMode
  onPermissionChange: (mode: PermissionMode) => void
  onSend: (prompt: string) => void
  onStop: () => void
}

/**
 * 输入条：Enter 发送 / Shift+Enter 换行；提交中按钮互斥禁用。
 * 草稿写进界面域（localStorage），刷新后能恢复——它只是 UI 便利，不进会话。
 *
 * 权限模式选择器挂在这里（提交动作的同域）：它是**本次提交**的一个参数，不是全局设置。
 * 运行中禁用——权限在 run 开始时定下，中途换档不会作用到正在跑的这一轮。
 */
export function Composer({
  busy,
  canSend,
  stopping = false,
  permission,
  onPermissionChange,
  onSend,
  onStop,
}: ComposerProps) {
  const { t } = useTranslation()
  const draft = useUiStore((state) => state.draft)
  const setDraft = useUiStore((state) => state.setDraft)
  const clearDraft = useUiStore((state) => state.clearDraft)
  const restored = useRef(false)

  useEffect(() => {
    if (!restored.current && draft) restored.current = true
  }, [draft])

  const submit = () => {
    const text = draft.trim()
    if (!text || busy || !canSend) return
    onSend(text)
    clearDraft()
  }

  return (
    <div className="sketch-chip tilt-input mx-0 flex flex-col gap-2 bg-input p-3">
      <div className="tilt-input-content flex flex-col gap-2">
        <TextArea
          aria-label={t('chat.placeholder')}
          placeholder={t('chat.placeholder')}
          rows={3}
          value={draft}
          disabled={!canSend}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
        />
        {restored.current && draft ? (
          <p className="text-xs text-ink/70">{t('chat.draft.restored')}</p>
        ) : null}
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div className="w-52 min-w-0">
            <PermissionSelector
              value={permission}
              onChange={onPermissionChange}
              disabled={busy || !canSend}
            />
          </div>
          <div className="flex items-center justify-end gap-2">
            {busy ? (
              <Button variant="danger" onClick={onStop} disabled={stopping} loading={stopping}>
                {stopping ? t('chat.stopping') : t('chat.stop')}
              </Button>
            ) : null}
            <Button variant="primary" onClick={submit} disabled={busy || !canSend}>
              {busy ? t('chat.sending') : t('chat.send')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
