/**
 * 权限按钮（输入区左侧的「态势」胶囊）：chip 反映当前权限模式（图标 + 文案
 * + 危险态着色），点击弹出卡片改模式。三态各有自己的图标：
 *   manual 手动 = user-check（人来裁决）；auto 自动 = shield-check（沙箱内）；
 *   full 完全 = alert-circle（关闭边界）。
 * full 走后端「三重锁」的界面侧：不能点一下就开——先出确认块，显式「确认开启」
 * 才回调（POST 时还要带 full_access_ack，见 StartRunInput）。
 * 当前模式由表面按选中会话的工作区默认权限初始化——按钮反映的是**实际态势**。
 */

import { useEffect, useState } from 'react'

import type { IconName } from '../../ui/Icon'
import type { PermissionMode } from '../../api/types'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

const MODES: Record<PermissionMode, { label: string; icon: IconName; desc: string }> = {
  manual: { label: '手动', icon: 'user-check', desc: '危险操作逐个问你' },
  auto: { label: '自动', icon: 'shield-check', desc: '分类器裁决，沙箱内执行' },
  full: { label: '完全', icon: 'alert-circle', desc: '关闭沙箱与网络边界' },
}

const MODE_ORDER: PermissionMode[] = ['manual', 'auto', 'full']

export type PermissionButtonProps = {
  mode: PermissionMode
  onChange: (mode: PermissionMode) => void
}

export function PermissionButton({ mode, onChange }: PermissionButtonProps) {
  const [open, setOpen] = useState(false)
  const [confirmingFull, setConfirmingFull] = useState(false)

  useEffect(() => {
    if (!open) setConfirmingFull(false)
  }, [open])

  const close = () => setOpen(false)

  const pick = (m: PermissionMode) => {
    if (m === mode) {
      close()
      return
    }
    if (m === 'full') {
      setConfirmingFull(true)
      return
    }
    onChange(m)
    close()
  }

  const confirmFull = () => {
    onChange('full')
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
          mode === 'full' ? 'border-transparent bg-danger/[0.08] text-danger' : 'border-hair text-ink-light',
        )}
      >
        <Icon name={MODES[mode].icon} size={12} />
        <span>{MODES[mode].label}</span>
        <span className="sr-only">权限模式</span>
      </button>

      {open && (
        <>
          <div data-testid="popover-backdrop" className="fixed inset-0 z-10" onClick={close} aria-hidden />
          <div
            role="dialog"
            aria-label="权限模式"
            className="absolute bottom-full left-0 z-20 mb-a8 w-[300px] rounded-md border-hairline border-hair bg-card p-a8 shadow-soft"
            style={{ animation: 'hana-scale-in var(--duration-slow) var(--ease-out)' }}
          >
            {MODE_ORDER.map((m) => {
              const meta = MODES[m]
              const selected = m === mode
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => pick(m)}
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
                  关闭沙箱与网络边界意味着工具可以无限制地执行与联网。仅在明确需要时开启。
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
