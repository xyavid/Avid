/**
 * 权限按钮（输入区左侧的「态势」胶囊）：chip 反映**这次运行是否完全访问**，
 * 点击弹出两态卡片——默认（毁灭级命令会问你一次）/ 完全访问（跳过确认、关沙箱）。
 * 完全访问不能点一下就开：先出确认块，显式「确认开启」才回调；降级回默认不设
 * 确认（收回授权是安全方向）。发送时 hook 按这个布尔附 full_access_ack
 * （见 StartRunInput）——界面只表达意图，服务端仍按显式凭据授权。
 */

import { useEffect, useState } from 'react'

import type { IconName } from '../../ui/Icon'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

const DEFAULT_META: { label: string; icon: IconName; desc: string } = {
  label: '默认',
  icon: 'user-check',
  desc: '毁灭级命令会问你一次',
}

const FULL_META: { label: string; icon: IconName; desc: string } = {
  label: '完全访问',
  icon: 'alert-circle',
  desc: '跳过确认、关沙箱',
}

const OPTIONS: { full: boolean; meta: typeof DEFAULT_META }[] = [
  { full: false, meta: DEFAULT_META },
  { full: true, meta: FULL_META },
]

export type PermissionButtonProps = {
  full: boolean
  onToggleFull: (full: boolean) => void
}

export function PermissionButton({ full, onToggleFull }: PermissionButtonProps) {
  const [open, setOpen] = useState(false)
  const [confirmingFull, setConfirmingFull] = useState(false)

  useEffect(() => {
    if (!open) setConfirmingFull(false)
  }, [open])

  const close = () => setOpen(false)
  const current = full ? FULL_META : DEFAULT_META

  const pick = (next: boolean) => {
    if (next === full) {
      close()
      return
    }
    if (next) {
      setConfirmingFull(true)
      return
    }
    onToggleFull(false)
    close()
  }

  const confirmFull = () => {
    onToggleFull(true)
    close()
  }

  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cx(
          'inline-flex h-[26px] items-center gap-[5px] rounded-sm border-hairline px-a8 font-ui text-hint font-medium transition-colors duration-fast ease-out hover:bg-overlay-light',
          full ? 'border-transparent bg-danger/[0.08] text-danger' : 'border-hair text-ink-light',
        )}
      >
        <Icon name={current.icon} size={12} />
        <span>{current.label}</span>
        <span className="sr-only">权限</span>
      </button>

      {open && (
        <>
          <div data-testid="popover-backdrop" className="fixed inset-0 z-10" onClick={close} aria-hidden />
          <div
            role="dialog"
            aria-label="权限"
            className="absolute bottom-full left-0 z-20 mb-a8 w-[300px] rounded-md border-hairline border-hair bg-card p-a8 shadow-soft"
            style={{ animation: 'hana-scale-in var(--duration-slow) var(--ease-out)' }}
          >
            {OPTIONS.map(({ full: value, meta }) => {
              const selected = value === full
              return (
                <button
                  key={String(value)}
                  type="button"
                  onClick={() => pick(value)}
                  className={cx(
                    'flex w-full items-start gap-a8 rounded-sm px-a8 py-a6 text-left transition-colors duration-fast ease-out',
                    selected ? 'bg-accent-light' : 'hover:bg-overlay-light',
                  )}
                >
                  <span className={cx('mt-[2px] shrink-0', selected ? 'text-accent' : 'text-ink-light')}>
                    <Icon name={meta.icon} size={14} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className={cx('block font-ui text-ui font-medium', selected ? 'text-accent' : 'text-ink')}>
                      {meta.label}
                    </span>
                    <span className="block font-ui text-hint text-ink-muted">{meta.desc}</span>
                  </span>
                  {selected && (
                    <span className="mt-[2px] shrink-0 text-accent">
                      <Icon name="check" size={14} />
                    </span>
                  )}
                </button>
              )
            })}

            {confirmingFull && (
              <div
                className="mt-a8 rounded-sm border-hairline bg-danger/[0.08] p-a8"
                style={{ animation: 'hana-fade-up var(--duration-fast) var(--ease-out)' }}
              >
                <p className="font-ui text-hint leading-[1.6] text-danger">
                  完全访问会跳过毁灭级确认、关闭沙箱且不过滤环境变量。仅在明确需要时开启。
                </p>
                <div className="mt-a8 flex items-center justify-end gap-a8">
                  <button
                    type="button"
                    onClick={() => setConfirmingFull(false)}
                    className="rounded-sm px-a8 py-a4 font-ui text-hint font-medium text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light"
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    onClick={confirmFull}
                    className="rounded-sm bg-danger px-a8 py-a4 font-ui text-hint font-medium text-card transition-colors duration-fast ease-out hover:opacity-90"
                  >
                    确认开启
                  </button>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </>
  )
}
