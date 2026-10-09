/**
 * 输入区（参考图主界面）：16px 圆角抬升面壳（--radius-chat-surface）+
 * 裸 textarea（自动增高，封顶约 8 行后内滚）+ 左侧权限/模型 + 右侧发送/停止钮。
 * Enter 发送，Shift+Enter 换行；**IME 合成中的 Enter 不发送**（中文输入法
 * 选词回车是组词，不是提交——纸本中文界面的硬约束）。
 * While busy the input stays editable: Enter = queue for the next turn, "insert" targets the
 * current run's next step; stop is cooperative, the terminal state comes from events.
 * 权限胶囊反映这次运行是否完全访问（默认 / 完全访问两态），发送时随
 * StartRunInput 提交（full 由 hook 附 full_access_ack）。
 *
 * Images: paste / drop / paperclip funnel into `addFiles`; chips are the draft and go out with
 * the text (images alone count). Over-limit images are downscaled by `prepareImage`; the server
 * only validates.
 */

import { useRef, useState } from 'react'

import type { ModelCandidate, UsageReport } from '../../api/types'
import type { DraftImage } from '../../state/imagePrep'
import { MAX_IMAGES, humanBytes, isImageFile, prepareImage } from '../../state/imagePrep'
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
  onSend: (text: string, images: DraftImage[]) => void
  /** Queue for the next turn while busy; omit to hide the button. */
  onQueue?: (text: string, images: DraftImage[]) => void
  /** Insert into the current run's next step while busy; omit to hide the button. */
  onInsert?: (text: string, images: DraftImage[]) => void
  onStop: () => void
  /** 本次运行的模型（providerId/modelId）；null = 还没选——那时发不出去，也不替用户猜。 */
  model?: string | null
  onChangeModel?: (model: string) => void
  /** 本次运行的推理强度（该模型声明的档位之一）；null = 不设 → 请求里不带这个参数。 */
  effort?: string | null
  onChangeEffort?: (effort: string | null) => void
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
  onQueue,
  onInsert,
  onStop,
  model = null,
  onChangeModel,
  effort = null,
  onChangeEffort,
  byokModels = [],
  usage = null,
}: ComposerProps) {
  const [text, setText] = useState('')
  const [images, setImages] = useState<DraftImage[]>([])
  const [imageError, setImageError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const composingRef = useRef(false)
  const areaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // 没选模型就不发车：显式带上模型，不由服务端的 chat 绑定替用户决定。
  // Images alone count; busy is not a block — queue/insert go through a separate path.
  const canSend =
    !disabled && (text.trim().length > 0 || images.length > 0) && model !== null

  const addFiles = async (files: File[]) => {
    setImageError(null)
    const picked = files.filter((file) => isImageFile(file.type))
    if (picked.length === 0 && files.length > 0) {
      setImageError('只收图片（png / jpeg / webp / gif）')
      return
    }
    const room = MAX_IMAGES - images.length
    if (picked.length > room) {
      setImageError(`一条消息最多 ${MAX_IMAGES} 张图`)
    }
    const accepted: DraftImage[] = []
    for (const file of picked.slice(0, Math.max(0, room))) {
      try {
        accepted.push(await prepareImage(file))
      } catch (e) {
        setImageError(e instanceof Error ? e.message : String(e))
      }
    }
    if (accepted.length > 0) setImages((cur) => [...cur, ...accepted].slice(0, MAX_IMAGES))
  }

  const removeImage = (id: string) => {
    setImages((cur) => {
      const gone = cur.find((image) => image.id === id)
      if (gone) URL.revokeObjectURL(gone.url)
      return cur.filter((image) => image.id !== id)
    })
  }

  const submit = (action: 'send' | 'queue' | 'insert' = 'send') => {
    if (!canSend) return
    // No queue channel while busy: a second run would be rejected (one run per session).
    if (busy && action === 'send') return
    const text_ = text.trim()
    if (action === 'queue') onQueue?.(text_, images)
    else if (action === 'insert') onInsert?.(text_, images)
    else onSend(text_, images)
    setText('')
    setImages([])
    setImageError(null)
    const el = areaRef.current
    if (el !== null) el.style.height = 'auto'
  }

  return (
    <div className="px-a16 pb-a16">
      <div
        onDragOver={(e) => {
          if (disabled) return
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          setDragging(false)
          if (disabled) return
          e.preventDefault()
          void addFiles([...e.dataTransfer.files])
        }}
        className={
          'relative mx-auto max-w-chat-input rounded-chat-surface border-hairline border-hair bg-card px-a16 py-a12 shadow-soft' +
          (dragging ? ' border-accent' : '')
        }
        data-dragging={dragging ? 'true' : undefined}
      >
        {images.length > 0 && (
          <div className="mb-a8 flex flex-wrap gap-a8" data-testid="composer-images">
            {images.map((image) => (
              <ImageChip key={image.id} image={image} onRemove={() => removeImage(image.id)} />
            ))}
          </div>
        )}
        <textarea
          ref={areaRef}
          rows={1}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            autogrow(e.target)
          }}
          onPaste={(e) => {
            const files = [...e.clipboardData.files]
            if (files.length > 0) {
              e.preventDefault()
              void addFiles(files)
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !composingRef.current) {
              e.preventDefault()
              // Enter queues while busy, sends when idle.
              submit(busy && onQueue ? 'queue' : 'send')
            }
          }}
          onCompositionStart={() => {
            composingRef.current = true
          }}
          onCompositionEnd={() => {
            composingRef.current = false
          }}
          placeholder={
            busy
              ? '运行中…Enter 排队等下一轮，「插入」进下一个 step'
              : '说点什么…（Enter 发送，Shift+Enter 换行）'
          }
          aria-label="消息输入"
          disabled={disabled}
          className={BARE_AREA}
        />
        {imageError !== null && (
          <p className="mt-a6 font-ui text-caption text-danger" role="alert">
            {imageError}
          </p>
        )}
        <div className="mt-a8 flex items-center justify-between">
          <div className="flex items-center gap-a8">
            <PermissionButton full={full} onToggleFull={onToggleFull} />
            {onChangeModel && (
              <ModelButton
                model={model}
                onChange={onChangeModel}
                effort={effort}
                onChangeEffort={onChangeEffort}
                candidates={byokModels}
              />
            )}
            <AttachButton
              disabled={disabled || images.length >= MAX_IMAGES}
              onClick={() => fileRef.current?.click()}
            />
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              multiple
              hidden
              onChange={(e) => {
                const files = [...(e.target.files ?? [])]
                e.target.value = ''
                void addFiles(files)
              }}
            />
            <ContextRing usage={usage} />
          </div>
          {busy ? (
            <div className="flex items-center gap-a8">
              {onQueue && (
                <ActionButton
                  icon="send"
                  label="排队"
                  disabled={!canSend}
                  onClick={() => submit('queue')}
                />
              )}
              {onInsert && (
                <ActionButton
                  icon="plus"
                  label="插入"
                  disabled={!canSend}
                  onClick={() => submit('insert')}
                />
              )}
              <StopButton onClick={onStop} />
            </div>
          ) : (
            <SendButton disabled={!canSend} onClick={() => submit('send')} />
          )}
        </div>
      </div>
    </div>
  )
}

/** A draft image chip: thumbnail, name and size; downscaled ones are marked. */
function ImageChip({ image, onRemove }: { image: DraftImage; onRemove: () => void }) {
  return (
    <span
      className="inline-flex items-center gap-a6 rounded-sm border-hairline border-hair bg-paper px-a8 py-a4"
      data-compressed={image.compressed ? 'true' : undefined}
    >
      <img src={image.url} alt={image.name} className="h-[28px] w-[28px] rounded-sm object-cover" />
      <span className="max-w-[160px] truncate font-ui text-caption text-ink">{image.name}</span>
      <span className="font-ui text-caption text-ink-muted">
        {humanBytes(image.bytes)}
        {image.compressed ? '（已压缩）' : ''}
      </span>
      <IconButton onClick={onRemove} label={`移除 ${image.name}`} />
    </span>
  )
}

function IconButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="text-ink-muted transition-colors duration-fast ease-out hover:text-ink"
    >
      <Icon name="x" size={12} />
    </button>
  )
}

/** Paperclip: opens the file picker; paste and drop are the other two entry points. */
function AttachButton({ disabled, onClick }: { disabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label="添加图片"
      title="添加图片（也可以直接粘贴或拖进来）"
      className="inline-flex h-[22px] items-center gap-a4 rounded-sm px-a6 font-ui text-caption text-ink-muted transition-colors duration-fast ease-out hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
    >
      <Icon name="paperclip" size={13} />
      图片
    </button>
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
