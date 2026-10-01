/**
 * 设置界面（报告 §7.5 的弹窗外壳：scrim 遮罩 + hana-scale-in 入场）：
 * - 模型段：可编辑的连接表单（对照参考图）——API 地址 / 协议 / 密钥 / 模型，
 *   保存进界面覆盖层（`~/.avid/model.toml`，优先于 .env），**对下一条消息立即生效**；
 *   密钥只入不出：输入框永远不预填，已配置时只给提示；「重置」清除覆盖层回落 .env。
 * - 外观段：模式三档（浅色 / 深色 / 跟随系统）+ 主题卡（暖纸 / 青夜）。
 * 关闭：右上 ✕ / Esc / 遮罩点击。
 */

import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { getModelSettings, resetModelSettings, saveModelSettings } from '../../api/client'
import type { ModelSettings, ModelSettingsInput } from '../../api/types'
import { resolveTheme, THEMES, useAppearance } from '../../state/appearance'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'
import { IconButton } from '../../ui/IconButton'
import { Input } from '../../ui/Input'
import { Tabs } from '../../ui/Tabs'

const MODE_LABELS: Record<string, string> = { light: '浅色', dark: '深色', auto: '跟随系统' }
const LABEL_TO_MODE: Record<string, 'light' | 'dark' | 'auto'> = {
  浅色: 'light',
  深色: 'dark',
  跟随系统: 'auto',
}

const PROVIDER_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '自动识别（按 API 地址）' },
  { value: 'openai', label: 'OpenAI 兼容（Chat Completions）' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'gemini', label: 'Gemini' },
]

type FormState = { base_url: string; provider: string; model: string; api_key: string }

function formFrom(settings: ModelSettings): FormState {
  return {
    base_url: settings.base_url ?? '',
    provider: settings.provider ?? '',
    model: settings.model ?? '',
    api_key: '',
  }
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="mt-a8 block">
      <span className="mb-a4 block font-ui text-hint text-ink-muted">{label}</span>
      {children}
    </label>
  )
}

export type SettingsModalProps = {
  onClose: () => void
}

export function SettingsModal({ onClose }: SettingsModalProps) {
  const { mode, setMode } = useAppearance()
  const resolved = resolveTheme(mode, window.matchMedia('(prefers-color-scheme: dark)').matches)

  const [settings, setSettings] = useState<ModelSettings | null>(null)
  const [form, setForm] = useState<FormState>({ base_url: '', provider: '', model: '', api_key: '' })
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)

  useEffect(() => {
    let alive = true
    getModelSettings()
      .then((s) => {
        if (!alive) return
        setSettings(s)
        setForm(formFrom(s))
      })
      .catch((e: unknown) => {
        if (alive) setStatus({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const save = async () => {
    setBusy(true)
    setStatus(null)
    try {
      const payload: ModelSettingsInput = {
        base_url: form.base_url.trim(),
        provider: form.provider === '' ? '' : (form.provider as ModelSettingsInput['provider']),
        model: form.model.trim(),
      }
      if (form.api_key.trim()) payload.api_key = form.api_key.trim()
      const next = await saveModelSettings(payload)
      setSettings(next)
      setForm(formFrom(next))
      setStatus({ kind: 'ok', text: '已保存，下一条消息立即使用这份配置' })
    } catch (e) {
      setStatus({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const reset = async () => {
    setBusy(true)
    setStatus(null)
    try {
      await resetModelSettings()
      const next = await getModelSettings()
      setSettings(next)
      setForm(formFrom(next))
      setStatus({ kind: 'ok', text: '已清除界面配置，回落 .env' })
    } catch (e) {
      setStatus({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

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
          <div className="mt-a8 rounded-md border-hairline border-hair bg-card p-a12">
            <p className="font-ui text-hint leading-[1.6] text-ink-muted">
              连接中转站、自部署服务或其他兼容 OpenAI / Anthropic / Gemini 协议的接口。
              保存后对新发送的消息立即生效；留空项回落 .env。
            </p>

            <Field label="API 地址">
              {/* 占位符不写完整 URL：A12 门禁要求 api/ 之外不出现第三方地址字面量 */}
              <Input
                value={form.base_url}
                onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                placeholder="中转站 / 自部署服务的接口地址，以 /v1 结尾"
              />
            </Field>

            <Field label="API 协议">
              <select
                value={form.provider}
                onChange={(e) => setForm((f) => ({ ...f, provider: e.target.value }))}
                className="h-control w-full rounded-sm border-hairline border-hair bg-card px-a8 font-ui text-ui text-ink transition-[border-color,box-shadow] duration-fast ease-out focus:border-accent focus:shadow-focus-ring focus:outline-none"
              >
                {PROVIDER_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="API 密钥">
              <Input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                placeholder={settings?.api_key_set ? '已配置 · 输入新值以替换' : '输入 API 密钥'}
                autoComplete="off"
              />
            </Field>

            <Field label="模型">
              <Input
                value={form.model}
                onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
                placeholder="例如 deepseek-chat"
              />
            </Field>

            <div className="mt-a16 flex items-center justify-between gap-a8">
              <span className="font-ui text-micro text-ink-muted">
                {settings?.overlay_active ? '界面配置已启用（优先于 .env）' : '当前来自 .env'}
              </span>
              <span className="flex items-center gap-a8">
                <button
                  type="button"
                  onClick={reset}
                  disabled={busy}
                  className="rounded-sm border-hairline border-hair px-a12 py-a4 font-ui text-hint font-medium text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                >
                  重置
                </button>
                <button
                  type="button"
                  onClick={save}
                  disabled={busy}
                  className="rounded-sm bg-accent px-a12 py-a4 font-ui text-hint font-medium text-card transition-colors duration-fast ease-out hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
                >
                  保存
                </button>
              </span>
            </div>
            {status && (
              <p
                className={cx(
                  'mt-a8 text-right font-ui text-hint',
                  status.kind === 'error' ? 'text-danger' : 'text-ink-light',
                )}
              >
                {status.text}
              </p>
            )}
          </div>
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
