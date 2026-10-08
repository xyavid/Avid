/**
 * 设置 → 模型：BYOK 三层配置的管理界面（阶段 34）。
 *
 * 三层模型：Provider（接入端点）1—N Model（id + 能力声明），Binding 把 Model 挂到
 * chat 槽位；「新增一个模型提供商只改配置，不改代码」。密钥只入不出：输入框永远
 * 不预填，GET 只给 key_set；**鉴权隐式**——填了密钥就按协议标准头发送，留空就
 * 不带（本地服务），没有 bearer/header 的选择。保存走整体 PUT（providers 全量 +
 * 绑定），服务端 validate 不过就不落盘。「测试连接」对**当前表单值**跑两步探测
 * （最小对话 + 工具冒烟），保存前就能测。
 *
 * 空态引导：没有任何提供商时提示先新增并在下方绑定 chat 槽位——BYOK 是模型
 * 连接的唯一来源，未绑定时运行会报「还没有模型配置」。
 */

import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import {
  getByokSettings,
  resetByokSettings,
  saveByokSettings,
  testByokModel,
} from '../../api/client'
import type {
  ByokProtocol,
  ByokSettings,
  ByokTestResult,
  ProviderEntry,
  ProviderInput,
} from '../../api/types'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'
import { Input } from '../../ui/Input'

const PROTOCOL_OPTIONS: { value: ByokProtocol; label: string }[] = [
  { value: 'openai-compatible', label: 'OpenAI 兼容（中转 / 自部署通用）' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'responses', label: 'OpenAI Responses（/responses 端点）' },
  { value: 'ollama', label: 'Ollama（本地）' },
]

const PROTOCOL_LABELS: Record<ByokProtocol, string> = {
  'openai-compatible': 'OpenAI 兼容',
  anthropic: 'Anthropic',
  responses: 'OpenAI Responses',
  ollama: 'Ollama',
}

const STATUS = 'mt-a8 font-ui text-hint'

function emptyDraft(): ProviderInput {
  return {
    id: '',
    label: '',
    protocol: 'openai-compatible',
    base_url: '',
    headers: {},
    extra_body: {},
    enabled: true,
    models: [],
    api_key: null,
  }
}

/** GET 回显 → PUT 载荷形状：剥掉 key_set；api_key 永远不预填（只入）。 */
function entryToInput(entry: ProviderEntry): ProviderInput {
  const { key_set: _keySet, ...rest } = entry
  return { ...rest, api_key: null }
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="mt-a8 block">
      <span className="mb-a4 flex items-baseline gap-a6">
        <span className="font-ui text-hint text-ink-muted">{label}</span>
        {hint && <span className="font-ui text-micro text-ink-muted">{hint}</span>}
      </span>
      {children}
    </label>
  )
}

const SELECT_CLS =
  'h-control w-full rounded-sm border-hairline border-hair bg-card px-a8 font-ui text-ui text-ink transition-[border-color,box-shadow] duration-fast ease-out focus:border-accent focus:shadow-focus-ring focus:outline-none'

export function ModelSection() {
  const [settings, setSettings] = useState<ByokSettings | null>(null)
  const [providers, setProviders] = useState<ProviderInput[]>([])
  const [chatBinding, setChatBinding] = useState<string>('')
  const [draft, setDraft] = useState<ProviderInput | null>(null)
  const [draftIsNew, setDraftIsNew] = useState(false)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, ByokTestResult>>({})

  useEffect(() => {
    let alive = true
    getByokSettings()
      .then((s) => {
        if (!alive) return
        setSettings(s)
        setProviders(s.providers.map(entryToInput))
        setChatBinding(s.bindings.chat ?? '')
      })
      .catch((e: unknown) => {
        if (alive) setStatus({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
      })
    return () => {
      alive = false
    }
  }, [])

  const commit = async (nextProviders: ProviderInput[], nextBinding: string) => {
    setBusy(true)
    setStatus(null)
    try {
      const next = await saveByokSettings({
        providers: nextProviders,
        bindings: { chat: nextBinding === '' ? null : nextBinding },
      })
      setSettings(next)
      setProviders(next.providers.map(entryToInput))
      setChatBinding(next.bindings.chat ?? '')
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
      await resetByokSettings()
      const next = await getByokSettings()
      setSettings(next)
      setProviders(next.providers.map(entryToInput))
      setChatBinding(next.bindings.chat ?? '')
      setTestResults({})
      setStatus({ kind: 'ok', text: '已清除 BYOK 配置与密钥' })
    } catch (e) {
      setStatus({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const runTest = async (provider: ProviderInput, modelId: string) => {
    setTesting(`${provider.id}/${modelId}`)
    setStatus(null)
    try {
      const result = await testByokModel(provider, modelId)
      setTestResults((prev) => ({ ...prev, [`${provider.id}/${modelId}`]: result }))
    } catch (e) {
      setStatus({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setTesting(null)
    }
  }

  const saveDraft = (next: ProviderInput) => {
    if (draftIsNew) {
      setProviders((list) => [...list, next])
    } else {
      setProviders((list) => list.map((p) => (p.id === draft?.id ? next : p)))
    }
    setDraft(null)
  }

  const bindingOptions = providers.flatMap((p) =>
    p.enabled
      ? p.models
          .filter((m) => m.capabilities.tool_calling !== false)
          .map((m) => ({ ref: `${p.id}/${m.id}`, label: `${p.label} · ${m.label || m.id}` }))
      : [],
  )

  return (
    <div>
      {/* 空态引导：模型连接只认 BYOK，没有任何提供商时先指路 */}
      {settings !== null && providers.length === 0 && (
        <div className="mt-a8 rounded-md border-hairline border-hair bg-card p-a12">
          <p className="font-ui text-hint leading-[1.6] text-ink-muted">
            还没有模型配置：点「新增提供商」填入接口地址与密钥，模型就会出现在输入区的
            模型选择里（实际用哪个由你在那里选）。下方那个默认模型供命令行与兜底使用。
          </p>
        </div>
      )}

      {/* 提供商列表 */}
      {providers.map((p) => {
        const entry = settings?.providers.find((e) => e.id === p.id)
        const result = Object.entries(testResults).find(([ref]) => ref.startsWith(`${p.id}/`))
        return (
          <div key={p.id} className="mt-a8 rounded-md border-hairline border-hair bg-card p-a12">
            <div className="flex items-center gap-a8">
              <span className={cx('font-ui text-ui font-medium', p.enabled ? 'text-ink' : 'text-ink-muted')}>
                {p.label}
              </span>
              <span className="font-ui text-micro text-ink-muted">{PROTOCOL_LABELS[p.protocol]}</span>
              {!p.enabled && <span className="font-ui text-micro text-danger">已停用</span>}
              <span className="ml-auto flex items-center gap-a4">
                <button
                  type="button"
                  onClick={() => {
                    setDraftIsNew(false)
                    setDraft(p)
                  }}
                  className="rounded-sm border-hairline border-hair px-a8 py-a2 font-ui text-micro text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink"
                >
                  编辑
                </button>
                <button
                  type="button"
                  onClick={() => setProviders((list) => list.filter((x) => x.id !== p.id))}
                  className="rounded-sm border-hairline border-hair px-a8 py-a2 font-ui text-micro text-danger transition-colors duration-fast ease-out hover:bg-overlay-light"
                >
                  删除
                </button>
              </span>
            </div>
            <p className="mt-a4 truncate font-ui text-micro text-ink-muted">{p.base_url}</p>
            <p className="mt-a4 font-ui text-micro text-ink-muted">
              {entry?.key_set || Boolean(p.api_key)
                ? '密钥已配置'
                : '未配密钥（留空 = 不发送鉴权头，本地服务适用）'}
            </p>
            {p.models.length > 0 && (
              <ul className="mt-a8 flex flex-col gap-a4">
                {p.models.map((m) => {
                  const ref = `${p.id}/${m.id}`
                  return (
                    <li key={m.id} className="flex items-center gap-a8">
                      <span className="min-w-0 truncate font-ui text-caption text-ink">{m.id}</span>
                      {m.capabilities.tool_calling === false && (
                        <span className="font-ui text-micro text-danger">不支持工具</span>
                      )}
                      <button
                        type="button"
                        onClick={() => runTest(p, m.id)}
                        disabled={testing !== null}
                        className="ml-auto shrink-0 rounded-sm border-hairline border-hair px-a8 py-a2 font-ui text-micro text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink disabled:opacity-40"
                      >
                        {testing === ref ? '测试中…' : '测试'}
                      </button>
                      {chatBinding === ref && (
                        <span className="shrink-0 text-accent">
                          <Icon name="check" size={12} />
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
            {result && (
              <ul className="mt-a8 flex flex-col gap-a4">
                {result[1].steps.map((s) => (
                  <li key={s.step} className={cx('font-ui text-micro', s.ok ? 'text-ink-light' : 'text-danger')}>
                    {s.ok ? '✓' : '✗'} {s.step === 'chat' ? '最小对话' : '工具冒烟'}：{s.detail}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )
      })}

      {/* 编辑 / 新增表单 */}
      {draft !== null ? (
        <ProviderEditor draft={draft} isNew={draftIsNew} onSave={saveDraft} onCancel={() => setDraft(null)} />
      ) : (
        <button
          type="button"
          onClick={() => {
            setDraftIsNew(true)
            setDraft(emptyDraft())
          }}
          className="mt-a8 rounded-sm border-hairline border-hair px-a12 py-a4 font-ui text-hint font-medium text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink"
        >
          新增提供商
        </button>
      )}

      {/* chat 绑定 + 保存 / 重置。阶段 54 起界面里每一个运行都显式带模型（用户在输入区选），
          所以这里的绑定是**兜底**：命令行、以及没有显式模型的调用才用它。 */}
      <Field
        label="默认模型（界面没选时兜底）"
        hint="命令行与没有显式模型的调用用它；界面里每个运行都会带上用户选的那个模型"
      >
        <select
          value={chatBinding}
          onChange={(e) => setChatBinding(e.target.value)}
          className={SELECT_CLS}
        >
          <option value="">未绑定（界面里仍可用——前提是先在输入区选模型）</option>
          {bindingOptions.map((o) => (
            <option key={o.ref} value={o.ref}>
              {o.label}
            </option>
          ))}
        </select>
      </Field>

      <div className="mt-a16 flex items-center justify-between gap-a8">
        <button
          type="button"
          onClick={reset}
          disabled={busy}
          className="rounded-sm border-hairline border-hair px-a12 py-a4 font-ui text-hint font-medium text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink disabled:opacity-40"
        >
          重置
        </button>
        <button
          type="button"
          onClick={() => commit(providers, chatBinding)}
          disabled={busy || draft !== null}
          className="rounded-sm bg-accent px-a12 py-a4 font-ui text-hint font-medium text-card transition-colors duration-fast ease-out hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
        >
          保存
        </button>
      </div>
      {status && (
        <p className={cx(STATUS, 'text-right', status.kind === 'error' ? 'text-danger' : 'text-ink-light')}>
          {status.text}
        </p>
      )}
    </div>
  )
}

type EditorProps = {
  draft: ProviderInput
  isNew: boolean
  onSave: (next: ProviderInput) => void
  onCancel: () => void
}

const ID_HINT = '小写字母、数字、连字符'

function ProviderEditor({ draft, isNew, onSave, onCancel }: EditorProps) {
  const [form, setForm] = useState<ProviderInput>(draft)
  const [headersText, setHeadersText] = useState(JSON.stringify(draft.headers, null, 2))
  const [extraText, setExtraText] = useState(JSON.stringify(draft.extra_body, null, 2))
  const [error, setError] = useState<string | null>(null)

  const patch = (part: Partial<ProviderInput>) => setForm((f) => ({ ...f, ...part }))

  const submit = () => {
    if (!/^[a-z0-9-]+$/.test(form.id)) {
      setError(`提供商 id 只能用${ID_HINT}：${form.id || '（空）'}`)
      return
    }
    if (!form.label.trim()) {
      setError('缺展示名（label）')
      return
    }
    if (!/^https?:\/\//.test(form.base_url.trim())) {
      setError('接口地址要以 http(s):// 开头；OpenAI 兼容端点通常以 /v1 结尾')
      return
    }
    let headers: Record<string, string>
    let extraBody: Record<string, unknown>
    try {
      headers = JSON.parse(headersText || '{}')
      extraBody = JSON.parse(extraText || '{}')
    } catch {
      setError('额外请求头 / 请求体不是合法 JSON')
      return
    }
    onSave({
      ...form,
      label: form.label.trim(),
      base_url: form.base_url.trim(),
      headers,
      extra_body: extraBody,
    })
  }

  return (
    <div className="mt-a8 rounded-md border-hairline border-hair bg-card p-a12">
      <div className="grid grid-cols-2 gap-a8">
        <Field label="id" hint={ID_HINT}>
          <Input
            value={form.id}
            onChange={(e) => patch({ id: e.target.value.trim() })}
            disabled={!isNew}
            placeholder="deepseek"
          />
        </Field>
        <Field label="展示名">
          <Input value={form.label} onChange={(e) => patch({ label: e.target.value })} placeholder="DeepSeek" />
        </Field>
      </div>

      <Field label="协议">
        <select value={form.protocol} onChange={(e) => patch({ protocol: e.target.value as ByokProtocol })} className={SELECT_CLS}>
          {PROTOCOL_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </Field>

      <Field label="接口地址" hint="OpenAI 兼容端点以 /v1 结尾">
        <Input
          value={form.base_url}
          onChange={(e) => patch({ base_url: e.target.value })}
          placeholder="中转站 / 自部署服务的接口地址"
        />
      </Field>

      <Field label="API 密钥" hint="留空 = 不发送鉴权头（本地服务）；已配置时留空 = 保持不变">
        <Input
          type="password"
          value={form.api_key ?? ''}
          onChange={(e) => patch({ api_key: e.target.value === '' ? null : e.target.value })}
          autoComplete="off"
          placeholder={isNew ? '输入 API 密钥（可留空）' : '已配置 · 输入新值以替换，空值并保存 = 清除'}
        />
      </Field>

      <Field label="模型" hint="至少一个；绑到 chat 槽位需要支持工具调用">
        <div className="flex flex-col gap-a4">
          {form.models.map((m, index) => (
            <div key={index} className="flex items-center gap-a4">
              <Input
                value={m.id}
                onChange={(e) =>
                  patch({
                    models: form.models.map((x, i) => (i === index ? { ...x, id: e.target.value } : x)),
                  })
                }
                placeholder="模型 id，例如 deepseek-chat"
              />
              <Input
                value={m.context_window?.toString() ?? ''}
                onChange={(e) =>
                  patch({
                    models: form.models.map((x, i) =>
                      i === index
                        ? { ...x, context_window: e.target.value ? Number(e.target.value) : null }
                        : x,
                    ),
                  })
                }
                placeholder="窗口"
                className="!w-[88px]"
              />
              <select
                value={m.capabilities.tool_calling === null || m.capabilities.tool_calling === undefined ? 'unset' : m.capabilities.tool_calling ? 'true' : 'false'}
                onChange={(e) =>
                  patch({
                    models: form.models.map((x, i) =>
                      i === index
                        ? {
                            ...x,
                            capabilities: {
                              ...x.capabilities,
                              tool_calling:
                                e.target.value === 'unset' ? null : e.target.value === 'true',
                            },
                          }
                        : x,
                    ),
                  })
                }
                className={cx(SELECT_CLS, '!h-control !w-[110px] shrink-0')}
                aria-label={`模型 ${m.id || '（未命名）'} 的工具调用能力`}
              >
                <option value="unset">工具：未声明</option>
                <option value="true">工具：支持</option>
                <option value="false">工具：不支持</option>
              </select>
              <button
                type="button"
                onClick={() => patch({ models: form.models.filter((_, i) => i !== index) })}
                className="shrink-0 text-ink-muted transition-colors duration-fast ease-out hover:text-danger"
                aria-label={`删除模型 ${m.id || '（未命名）'}`}
              >
                <Icon name="x" size={14} />
              </button>
            </div>
          ))}
          <button
            type="button"
            aria-label="添加模型"
            onClick={() =>
              patch({
                models: [
                  ...form.models,
                  { id: '', label: null, context_window: null, max_output: null, capabilities: { tool_calling: true } },
                ],
              })
            }
            className="self-start rounded-sm border-hairline border-hair px-a8 py-a2 font-ui text-micro text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink"
          >
            添加模型
          </button>
        </div>
      </Field>

      <details className="mt-a8">
        <summary className="cursor-pointer font-ui text-micro text-ink-muted">高级：额外请求头 / 请求体（JSON）</summary>
        <Field label="额外请求头" hint='JSON 对象，例如 {"X-Trace": "avid"}'>
          <textarea value={headersText} onChange={(e) => setHeadersText(e.target.value)} rows={3} className="w-full rounded-sm border-hairline border-hair bg-card p-a8 font-mono text-micro text-ink focus:border-accent focus:outline-none" />
        </Field>
        <Field label="透传请求体字段" hint="合并进每次请求体（路由参数等）">
          <textarea value={extraText} onChange={(e) => setExtraText(e.target.value)} rows={3} className="w-full rounded-sm border-hairline border-hair bg-card p-a8 font-mono text-micro text-ink focus:border-accent focus:outline-none" />
        </Field>
      </details>

      {error && <p className={cx(STATUS, 'text-danger')}>{error}</p>}

      <div className="mt-a12 flex items-center justify-end gap-a8">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-sm border-hairline border-hair px-a12 py-a4 font-ui text-hint font-medium text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink"
        >
          取消
        </button>
        <button
          type="button"
          onClick={submit}
          className="rounded-sm bg-accent px-a12 py-a4 font-ui text-hint font-medium text-card transition-colors duration-fast ease-out hover:bg-accent-hover"
        >
          完成
        </button>
      </div>
    </div>
  )
}
