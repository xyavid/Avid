/**
 * 设置界面（报告 §7.5 的弹窗外壳：scrim 遮罩 + hana-scale-in 入场）：
 * - 模型段：BYOK 三层配置管理（阶段 34，见 ModelSection）——Provider / Model /
 *   Binding 全部由配置描述，密钥只入不出，保存后对下一条消息立即生效；
 * - 外观段：模式三档（浅色 / 深色 / 跟随系统）+ 主题卡（暖纸 / 青夜）。
 * 关闭：右上 ✕ / Esc / 遮罩点击。
 */

import { useEffect } from 'react'

import { resolveTheme, THEMES, useAppearance } from '../../state/appearance'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'
import { IconButton } from '../../ui/IconButton'
import { Tabs } from '../../ui/Tabs'
import { ModelSection } from './ModelSection'

const MODE_LABELS: Record<string, string> = { light: '浅色', dark: '深色', auto: '跟随系统' }
const LABEL_TO_MODE: Record<string, 'light' | 'dark' | 'auto'> = {
  浅色: 'light',
  深色: 'dark',
  跟随系统: 'auto',
}

export type SettingsModalProps = {
  onClose: () => void
}

export function SettingsModal({ onClose }: SettingsModalProps) {
  const { mode, setMode } = useAppearance()
  const resolved = resolveTheme(mode, window.matchMedia('(prefers-color-scheme: dark)').matches)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[8vh]" role="presentation">
      <div className="absolute inset-0 bg-[var(--scrim-30)]" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="设置"
        className="relative max-h-[84vh] w-[560px] max-w-[92vw] overflow-y-auto scroll-auto rounded-lg border-hairline border-hair bg-paper p-a24 shadow-soft"
        style={{ animation: 'hana-scale-in var(--duration-slow) var(--ease-out)' }}
      >
        <div className="flex items-center justify-between">
          <h2 className="font-serif text-title font-medium text-ink">设置</h2>
          <IconButton icon="x" label="关闭" onClick={onClose} />
        </div>

        <section className="mt-a24">
          <h3 className="font-ui text-hint font-medium text-ink-muted">模型</h3>
          <p className="mt-a8 font-ui text-hint leading-[1.6] text-ink-muted">
            连接中转站、自部署服务或本地模型（OpenAI 兼容 / Responses / Anthropic 协议）。
            密钥只写入本机密钥文件，任何界面与日志都不会回传。
          </p>
          <ModelSection />
        </section>

        <section className="mt-a24">
          <h3 className="font-ui text-hint font-medium text-ink-muted">外观</h3>
          <div className="mt-a8 rounded-md border-hairline border-hair bg-card p-a12">
            <Tabs
              items={['浅色', '深色', '跟随系统']}
              value={MODE_LABELS[mode] ?? '跟随系统'}
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
