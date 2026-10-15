/**
 * Settings → Web: the search key (write-only) plus the endpoint and timeout, each showing who
 * decides it — an environment variable wins over the settings file, and then that field is
 * read-only, because saving something the env var overrides would fake a successful save.
 * "Test" runs one real search through the tool's own core, so a green line here means the tools
 * work. Reaching private addresses is deliberately not switchable here: that guardrail stays an
 * environment variable with friction.
 */

import { useEffect, useState } from 'react'

import { getWebSettings, saveWebSettings, testWebSearch } from '../../api/client'
import type { WebSettings } from '../../api/types'
import { Button } from '../../ui/Button'
import { cx } from '../../ui/cx'
import { Input } from '../../ui/Input'

const STATUS = 'mt-a8 font-ui text-hint'
const HINT = 'font-ui text-micro leading-[1.6] text-ink-muted'

type Source = WebSettings['search_url_source']

export function sourceLabel(source: Source, envName: string): string {
  if (source === 'env') return `环境变量 ${envName}`
  if (source === 'settings') return '设置文件 settings.json'
  return '默认值'
}

export function WebSection() {
  const [current, setCurrent] = useState<WebSettings | null>(null)
  const [keyDraft, setKeyDraft] = useState('')
  const [urlDraft, setUrlDraft] = useState('')
  const [timeoutDraft, setTimeoutDraft] = useState('')
  const [status, setStatus] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)

  const apply = (state: WebSettings) => {
    setCurrent(state)
    setKeyDraft('')
    setUrlDraft(state.search_url)
    setTimeoutDraft(state.timeout_seconds.toString())
  }

  const fail = (e: unknown) => setStatus({ kind: 'error', text: e instanceof Error ? e.message : String(e) })

  useEffect(() => {
    const controller = new AbortController()
    getWebSettings(controller.signal)
      .then(apply)
      .catch((e: unknown) => {
        if (!controller.signal.aborted) fail(e)
      })
    return () => controller.abort()
  }, [])

  const urlLocked = current?.search_url_source === 'env'
  const timeoutLocked = current?.timeout_source === 'env'

  /** Only a non-empty box sends a key: an empty one means "leave the stored key alone". */
  const keyPayload = () => (keyDraft.trim() ? keyDraft.trim() : null)

  const save = async () => {
    setBusy(true)
    try {
      const state = await saveWebSettings({
        api_key: keyPayload(),
        search_url: urlDraft.trim(),
        timeout_seconds: timeoutDraft.trim() ? Number(timeoutDraft.trim()) : null,
      })
      apply(state)
      setStatus({ kind: 'ok', text: '已保存；下一次调用立即生效，不用重启。' })
    } catch (e: unknown) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const clearKey = async () => {
    setBusy(true)
    try {
      apply(await saveWebSettings({ api_key: '', search_url: urlDraft.trim(), timeout_seconds: null }))
      setStatus({ kind: 'ok', text: '已清除 Key：两个联网工具会失败关闭，其余能力不受影响。' })
    } catch (e: unknown) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const runTest = async () => {
    setTesting(true)
    try {
      const result = await testWebSearch({
        api_key: keyPayload(),
        search_url: urlDraft.trim() || null,
      })
      setStatus({ kind: result.ok ? 'ok' : 'error', text: result.message })
    } catch (e: unknown) {
      fail(e)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="mt-a8 rounded-md border-hairline border-hair bg-card p-a12" data-testid="web-settings">
      <div className="flex items-baseline gap-a6">
        <span className="font-ui text-hint text-ink-muted">检索服务</span>
        <span className="font-ui text-micro text-ink-muted">
          {current ? current.secret_ref : '（读取中…）'}
        </span>
        {current && (
          <a
            className="ml-auto font-ui text-micro text-accent"
            href={current.signup_url}
            target="_blank"
            rel="noreferrer"
          >
            申请 Key
          </a>
        )}
      </div>

      <div className="mt-a8">
        <div className="flex items-baseline gap-a6">
          <span className="font-ui text-hint text-ink-muted">密钥</span>
          <span className="font-ui text-micro text-ink-muted">
            {current?.key_set ? '已配置' : '未配置（两个联网工具会失败关闭）'}
          </span>
        </div>
        <div className="mt-a4 flex items-center gap-a8">
          <Input
            type="password"
            value={keyDraft}
            onChange={(e) => setKeyDraft(e.target.value)}
            autoComplete="off"
            disabled={busy}
            placeholder={current?.key_set ? '已配置 · 输入新值以替换' : '粘贴 Key，例如 tvly-…'}
            aria-label="联网检索密钥"
          />
          <Button
            className="shrink-0 whitespace-nowrap"
            onClick={clearKey}
            disabled={busy || !current?.key_set}
          >
            清除 Key
          </Button>
        </div>
        {current && (
          <p className={cx(HINT, 'mt-a4')}>
            只写入 {current.secrets_file} 的 {current.secret_ref} 字段（0600，与模型 Key 同库），
            任何界面与日志都不会回传。
          </p>
        )}
      </div>

      <div className="mt-a8">
        <div className="flex items-baseline gap-a6">
          <span className="font-ui text-hint text-ink-muted">接口根地址</span>
          {current && (
            <span className="font-ui text-micro text-ink-muted">
              来源：{sourceLabel(current.search_url_source, current.env_search_url)}
            </span>
          )}
        </div>
        <Input
          className="mt-a4"
          value={urlDraft}
          onChange={(e) => setUrlDraft(e.target.value)}
          disabled={busy || urlLocked}
          placeholder={current?.default_search_url ?? ''}
          aria-label="检索接口根地址"
        />
      </div>

      <div className="mt-a8">
        <div className="flex items-baseline gap-a6">
          <span className="font-ui text-hint text-ink-muted">单次超时（秒）</span>
          {current && (
            <span className="font-ui text-micro text-ink-muted">
              来源：{sourceLabel(current.timeout_source, current.env_timeout)}
            </span>
          )}
        </div>
        <Input
          className="mt-a4"
          value={timeoutDraft}
          onChange={(e) => setTimeoutDraft(e.target.value)}
          disabled={busy || timeoutLocked}
          placeholder={current ? current.default_timeout_seconds.toString() : ''}
          aria-label="检索超时秒数"
        />
      </div>

      <div className="mt-a8 flex items-center gap-a8">
        <Button className="shrink-0 whitespace-nowrap" onClick={save} disabled={busy}>
          保存联网设置
        </Button>
        <Button className="shrink-0 whitespace-nowrap" onClick={runTest} disabled={busy || testing}>
          {testing ? '测试中…' : '测试连接'}
        </Button>
      </div>

      <p className={cx(HINT, 'mt-a8')}>
        地址留空、超时留空都回到默认值。
        {urlLocked || timeoutLocked
          ? ' 带「环境变量」来源的那一项由环境变量说了算，改这里不生效；去掉那个变量再来改。'
          : ''}
      </p>
      <p className={cx(HINT, 'mt-a4')}>
        抓取工具
        {current?.allow_private_hosts
          ? `当前允许本机与内网地址（${current.env_allow_private} 已打开）。`
          : `只取公网地址：本机与内网一律拒绝，免得把本地服务当成网页。内网站点要取时，设 ${current?.env_allow_private ?? 'AVID_WEB_ALLOW_PRIVATE_HOSTS'}=1 后重新启动服务。`}
      </p>
      {status && (
        <p className={cx(STATUS, status.kind === 'error' ? 'text-danger' : 'text-ink-light')}>
          {status.text}
        </p>
      )}
    </div>
  )
}
