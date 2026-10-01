/**
 * 设置界面（报告 §7.5 的弹窗外壳：scrim 遮罩 + hana-scale-in 入场）：
 * - 模型段：当前模型（meta 实数据）+ 配置出处说明（服务端 .env，改后重启）；
 * - 外观段：模式三档（浅色 / 深色 / 跟随系统）+ 主题卡（暖纸 / 青夜，
 *   点选即把模式定到该主题的明暗归属）。
 * 关闭：右上 ✕ / Esc / 遮罩点击。外观写入 localStorage，全局即时生效。
 */

import { useEffect } from 'react'

import type { AppearanceMode } from '../../state/appearance'
import { resolveTheme, THEMES, useAppearance } from '../../state/appearance'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'
import { IconButton } from '../../ui/IconButton'
import { Tabs } from '../../ui/Tabs'

const MODE_LABELS: Record<AppearanceMode, string> = { light: '浅色', dark: '深色', auto: '跟随系统' }
const LABEL_TO_MODE: Record<string, AppearanceMode> = { 浅色: 'light', 深色: 'dark', 跟随系统: 'auto' }

export type SettingsModalProps = {
  model: string | null
  onClose: () => void
}

export function SettingsModal({ model, onClose }: SettingsModalProps) {
  const { mode, setMode } = useAppearance()
  const resolved = resolveTheme(mode, window.matchMedia('(prefers-color-scheme: dark)').matches)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh]" role="presentation">
      <div className="absolute inset-0 bg-[var(--scrim-30)]" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="设置"
        className="relative w-[560px] max-w-[92vw] rounded-lg border-hairline border-hair bg-paper p-a24 shadow-soft"
        style={{ animation: 'hana-scale-in var(--duration-slow) var(--ease-out)' }}
      >
        <div className="flex items-center justify-between">
          <h2 className="font-serif text-title font-medium text-ink">设置</h2>
          <IconButton icon="x" label="关闭" onClick={onClose} />
        </div>

        <section className="mt-a24">
          <h3 className="font-ui text-hint font-medium text-ink-muted">模型</h3>
          <div className="mt-a8 rounded-md border-hairline border-hair bg-card p-a12">
            <div className="flex items-baseline justify-between gap-a8">
              <span className="font-ui text-hint text-ink-muted">当前模型</span>
              <span className="truncate font-mono text-ui text-ink">{model ?? '—'}</span>
            </div>
            <p className="mt-a8 font-ui text-hint leading-[1.6] text-ink-muted">
              模型、密钥与接口地址来自服务端 .env（AVID_MODEL / AVID_API_KEY / AVID_BASE_URL）；
              改动后重启 avid web 生效。
            </p>
          </div>
        </section>

        <section className="mt-a24">
          <h3 className="font-ui text-hint font-medium text-ink-muted">外观</h3>
          <div className="mt-a8 rounded-md border-hairline border-hair bg-card p-a12">
            <Tabs
              items={['浅色', '深色', '跟随系统']}
              value={MODE_LABELS[mode]}
              onChange={(label) => setMode(LABEL_TO_MODE[label] ?? 'auto')}
            />
            <div className="mt-a12 flex flex-wrap gap-a12">
              {THEMES.map((t) => {
                const active = t.id === resolved
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => setMode(t.kind)}
                    className={cx(
                      'flex w-[168px] flex-col gap-a8 rounded-md border-hairline p-a8 text-left transition-colors duration-fast ease-out',
                      active ? 'border-transparent bg-accent-light' : 'border-hair bg-card hover:bg-overlay-light',
                    )}
                  >
                    <span className="flex items-center gap-a8">
                      <span className="flex h-[22px] w-[30px] shrink-0 overflow-hidden rounded-sm border-hairline border-hair">
                        <span className="h-full flex-1" style={{ background: t.bg }} />
                        <span className="h-full w-[10px]" style={{ background: t.accent }} />
                      </span>
                      <span className={cx('font-ui text-ui', active ? 'font-medium text-accent' : 'text-ink')}>
                        {t.label}
                      </span>
                      <span className="ml-auto text-ink-muted">
                        <Icon name={t.kind === 'dark' ? 'moon' : 'sun'} size={12} />
                      </span>
                    </span>
                    <span className="font-ui text-micro text-ink-muted">
                      {t.kind === 'dark' ? '深色' : '浅色'}
                      {active && mode === 'auto' ? ' · 跟随系统' : ''}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
