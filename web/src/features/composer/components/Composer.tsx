import { useEffect, useRef } from 'react'

import { Button, TextArea } from '../../../ui/primitives'
import { useTranslation } from '../../../lib/i18n'
import { useUiStore } from '../../../state/uiStore'

export interface ComposerProps {
  busy: boolean
  canSend: boolean
  stopping?: boolean
  onSend: (prompt: string) => void
  onStop: () => void
}

/**
 * 输入条：Enter 发送 / Shift+Enter 换行；提交中按钮互斥禁用。
 * 草稿写进界面域（localStorage），刷新后能恢复——它只是 UI 便利，不进会话。
 */
export function Composer({ busy, canSend, stopping = false, onSend, onStop }: ComposerProps) {
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
  )
}
