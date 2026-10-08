/**
 * 输入区（参考图主界面）：16px 圆角抬升面壳（--radius-chat-surface）+
 * 裸 textarea（自动增高，封顶约 8 行后内滚）+ 左侧权限/模型 + 右侧发送/停止钮。
 * Enter 发送，Shift+Enter 换行；**IME 合成中的 Enter 不发送**（中文输入法
 * 选词回车是组词，不是提交——纸本中文界面的硬约束）。
 * 运行中（busy）输入保持可编辑（先写好下一条），只禁发送（钮变停止）；
 * 停止是协作式的，终态以事件为准。
 * 权限胶囊反映这次运行是否完全访问（默认 / 完全访问两态），发送时随
 * StartRunInput 提交（full 由 hook 附 full_access_ack）。
 */

import { useRef, useState } from 'react'

import type { ModelCandidate, UsageReport } from '../../api/types'
import { Icon, type IconName } from '../../ui/Icon'
import { ContextRing } from './ContextRing'
import { PermissionButton } from './PermissionButton'
import { ModelButton } from './ModelButton'

const BARE_AREA =
  'max-h-[216px] w-full resize-none overflow-y-auto bg-transparent font-ui text-ui leading-[24px] text-ink placeholder:text-ink-muted focus:outline-none disabled:opacity-40'

/** 自动增高：先缩回 auto 量出 scrollHeight，再夹到 8 行封顶。 */
function autogrow(el: HTMLTextAreaElement) {
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 216)}px`
}

export type ComposerProps = {
  /** 这次运行是否完全访问（默认 false = normal）。 */
  full: boolean
  onToggleFull: (full: boolean) => void
  /** 无选中会话等：整条输入路径不可用。 */
  disabled?: boolean
  /** 运行中：发送禁用（钮变停止），输入仍可编辑。 */
  busy?: boolean
  onSend: (text: string) => void
  onStop: () => void
  /** 本次运行的模型（providerId/modelId）；null = 还没选——那时发不出去，也不替用户猜。 */
  model?: string | null
  onChangeModel?: (model: string) => void
  /** BYOK 候选（providerId/modelId ref）：模型胶囊的全部选项来自这里。 */
  byokModels?: ModelCandidate[]
  /** 上下文用量快照（运行中吃事件里的每轮读数，收尾后是落盘那份）。 */
  usage?: UsageReport | null
}

export function Composer({
  full,
  onToggleFull,
  disabled = false,
  busy = false,
  onSend,
  onStop,
  model = null,
  onChangeModel,
  byokModels = [],
  usage = null,
}: ComposerProps) {
  const [text, setText] = useState('')
  const composingRef = useRef(false)
  const areaRef = useRef<HTMLTextAreaElement>(null)
  // 没选模型就不发车：显式带上模型，不由服务端的 chat 绑定替用户决定
  const canSend = !disabled && !busy && text.trim().length > 0 && model !== null

  const submit = () => {
    if (!canSend) return
    onSend(text.trim())
    setText('')
    const el = areaRef.current
    if (el !== null) el.style.height = 'auto'
  }

  return (
    <div className="px-a16 pb-a16">
      <div className="relative mx-auto max-w-chat-input rounded-chat-surface border-hairline border-hair bg-card px-a16 py-a12 shadow-soft">
        <textarea
          ref={areaRef}
          rows={1}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            autogrow(e.target)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !composingRef.current) {
              e.preventDefault()
              submit()
            }
          }}
          onCompositionStart={() => {
            composingRef.current = true
          }}
          onCompositionEnd={() => {
            composingRef.current = false
          }}
          placeholder={busy ? '运行中…输入可以先写好' : '说点什么…（Enter 发送，Shift+Enter 换行）'}
          aria-label="消息输入"
          disabled={disabled}
          className={BARE_AREA}
        />
        <div className="mt-a8 flex items-center justify-between">
          <div className="flex items-center gap-a8">
            <PermissionButton full={full} onToggleFull={onToggleFull} />
            {onChangeModel && (
              <ModelButton model={model} onChange={onChangeModel} candidates={byokModels} />
            )}
            <ContextRing usage={usage} />
          </div>
          {busy ? (
            <StopButton onClick={onStop} />
          ) : (
            <SendButton disabled={!canSend} onClick={submit} />
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

function SendButton({ disabled, onClick }: { disabled: boolean; onClick: () => void }) {
  return <ActionButton icon="send" label="发送" disabled={disabled} onClick={onClick} />
}

function StopButton({ onClick }: { onClick: () => void }) {
  return <ActionButton icon="square" label="停止" onClick={onClick} />
}
