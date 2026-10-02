/**
 * 输入区（参考图主界面）：16px 圆角抬升面壳（--radius-chat-surface）+
 * 裸输入框（bare Input）+ 左侧 [附加·附件·权限] + 右侧发送/停止钮。
 * 发送与停止是**带文字**的按钮（图标 + 「发送」/「停止」）：这两个动作每次都要认，
 * 只给图标时得先认图；文字也让它们的可访问名直接等于用户看到的字。
 * Enter 发送（trim 后非空）；运行中（busy）输入禁用、发送钮变停止钮
 * （square 图标，仍是 accent 实底——停止是协作式的，终态以事件为准）。
 * 权限胶囊反映实际三态，发送时随 StartRunInput 提交（full 由 hook 附 ack）。
 */

import { useState } from 'react'

import type { ModelCandidate, PermissionMode } from '../../api/types'
import { Icon, type IconName } from '../../ui/Icon'
import { IconButton } from '../../ui/IconButton'
import { Input } from '../../ui/Input'
import { ModelButton } from './ModelButton'
import { PermissionButton } from './PermissionButton'

export type ComposerProps = {
  permission: PermissionMode
  onChangePermission: (mode: PermissionMode) => void
  /** 无选中会话等：整条输入路径不可用。 */
  disabled?: boolean
  /** 运行中：输入禁用，发送钮变停止钮。 */
  busy?: boolean
  onSend: (text: string) => void
  onStop: () => void
  /** 本次运行的模型覆盖；null = 跟随设置。 */
  model?: string | null
  onChangeModel?: (model: string | null) => void
  /** 设置里解析出来的模型（展示用）。 */
  effectiveModel?: string | null
  knownModels?: string[]
  /** BYOK 候选（providerId/modelId ref）；非空时模型胶囊优先展示。 */
  byokModels?: ModelCandidate[]
}

export function Composer({
  permission,
  onChangePermission,
  disabled = false,
  busy = false,
  onSend,
  onStop,
  model = null,
  onChangeModel,
  effectiveModel = null,
  knownModels = [],
  byokModels = [],
}: ComposerProps) {
  const [text, setText] = useState('')
  const locked = disabled || busy
  const canSend = !locked && text.trim().length > 0

  const submit = () => {
    if (!canSend) return
    onSend(text.trim())
    setText('')
  }

  return (
    <div className="px-a16 pb-a16">
      <div className="relative mx-auto max-w-chat-input rounded-chat-surface border-hairline border-hair bg-card px-a16 py-a12 shadow-soft">
        <Input
          bare
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              submit()
            }
          }}
          placeholder={busy ? '运行中…可点右侧停止' : '说点什么…'}
          aria-label="消息输入"
          disabled={locked}
        />
        <div className="mt-a8 flex items-center justify-between">
          <div className="flex items-center gap-a8">
            <IconButton icon="plus" label="附加" disabled />
            <IconButton icon="paperclip" label="附件" disabled />
            <PermissionButton mode={permission} onChange={onChangePermission} />
            {onChangeModel && (
              <ModelButton
                model={model}
                onChange={onChangeModel}
                effective={effectiveModel}
                known={knownModels}
                candidates={byokModels}
              />
            )}
          </div>
          {busy ? (
            <ActionButton icon="square" label="停止" onClick={onStop} />
          ) : (
            <ActionButton icon="send" label="发送" disabled={!canSend} onClick={submit} />
          )}
        </div>
      </div>
    </div>
  )
}

/** 主行动按钮：accent 实底 + 图标 + 文字（发送 / 停止 两个动作用它）。 */
function ActionButton({
  icon,
  label,
  disabled = false,
  onClick,
}: {
  icon: IconName
  label: string
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="inline-flex h-[26px] items-center gap-a6 rounded-sm bg-accent px-a10 font-ui text-caption text-card transition-colors duration-fast ease-out hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
    >
      <Icon name={icon} size={13} />
      {label}
    </button>
  )
}
