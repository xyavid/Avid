// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WebSettings } from '../../../api/types'
import { WebSection } from '../WebSection'

const getWebSettings = vi.fn()
const saveWebSettings = vi.fn()
const testWebSearch = vi.fn()

vi.mock('../../../api/client', () => ({
  ApiError: class ApiError extends Error {},
  getWebSettings: (...a: unknown[]) => getWebSettings(...a),
  saveWebSettings: (...a: unknown[]) => saveWebSettings(...a),
  testWebSearch: (...a: unknown[]) => testWebSearch(...a),
}))

const DEFAULT: WebSettings = {
  key_set: false,
  search_url: 'https://api.tavily.com',
  default_search_url: 'https://api.tavily.com',
  search_url_source: 'default',
  timeout_seconds: 30,
  default_timeout_seconds: 30,
  timeout_source: 'default',
  allow_private_hosts: false,
  env_search_url: 'AVID_WEB_SEARCH_URL',
  env_timeout: 'AVID_WEB_TIMEOUT_SECONDS',
  env_allow_private: 'AVID_WEB_ALLOW_PRIVATE_HOSTS',
  signup_url: 'https://app.tavily.com',
  secret_ref: 'tavily',
  secrets_file: '/home/u/.avid/secrets.json',
}

async function ready() {
  render(<WebSection />)
  await waitFor(() => expect(screen.getByLabelText('检索接口根地址')).toBeTruthy())
}

beforeEach(() => {
  getWebSettings.mockReset().mockResolvedValue(DEFAULT)
  saveWebSettings.mockReset()
  testWebSearch.mockReset()
})
afterEach(cleanup)

describe('WebSection（设置 · 联网）', () => {
  it('未配置时说明两个工具会失败关闭，并显示默认端点与来源', async () => {
    await ready()

    expect(screen.getByText(/未配置（两个联网工具会失败关闭）/)).toBeTruthy()
    expect(screen.getByText(/只取公网地址/)).toBeTruthy()
    expect(screen.getByText(/AVID_WEB_ALLOW_PRIVATE_HOSTS=1/)).toBeTruthy()
    expect((screen.getByLabelText('检索接口根地址') as HTMLInputElement).value).toBe(
      'https://api.tavily.com',
    )
    expect((screen.getByLabelText('检索超时秒数') as HTMLInputElement).value).toBe('30')
    expect((screen.getByRole('button', { name: '清除 Key' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('填 Key 并保存：密钥写进请求体，端点与超时按当前值提交', async () => {
    saveWebSettings.mockResolvedValue({ ...DEFAULT, key_set: true })
    await ready()

    fireEvent.change(screen.getByLabelText('联网检索密钥'), { target: { value: 'tvly-abc' } })
    fireEvent.click(screen.getByRole('button', { name: '保存联网设置' }))

    await waitFor(() =>
      expect(saveWebSettings).toHaveBeenCalledWith({
        api_key: 'tvly-abc',
        search_url: 'https://api.tavily.com',
        timeout_seconds: 30,
      }),
    )
    await waitFor(() => expect(screen.getByText(/已保存；下一次调用立即生效/)).toBeTruthy())
  })

  it('没填 Key 时保存只改端点，api_key 是 null（不动已存的密钥）', async () => {
    saveWebSettings.mockResolvedValue(DEFAULT)
    await ready()

    fireEvent.change(screen.getByLabelText('检索接口根地址'), {
      target: { value: 'https://search.internal/v1' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存联网设置' }))

    await waitFor(() =>
      expect(saveWebSettings).toHaveBeenCalledWith({
        api_key: null,
        search_url: 'https://search.internal/v1',
        timeout_seconds: 30,
      }),
    )
  })

  it('清除 Key 发空串（服务端把空串当作删除），并说明影响范围', async () => {
    getWebSettings.mockResolvedValue({ ...DEFAULT, key_set: true })
    saveWebSettings.mockResolvedValue(DEFAULT)
    await ready()

    fireEvent.click(screen.getByRole('button', { name: '清除 Key' }))

    await waitFor(() =>
      expect(saveWebSettings).toHaveBeenCalledWith({
        api_key: '',
        search_url: 'https://api.tavily.com',
        timeout_seconds: null,
      }),
    )
    await waitFor(() => expect(screen.getByText(/已清除 Key/)).toBeTruthy())
  })

  it('环境变量赢时那一项只读，并说清改这里不生效', async () => {
    getWebSettings.mockResolvedValue({
      ...DEFAULT,
      search_url_source: 'env',
      search_url: 'https://env.example/v2',
    })
    await ready()

    expect((screen.getByLabelText('检索接口根地址') as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByText(/环境变量 AVID_WEB_SEARCH_URL/)).toBeTruthy()
    expect(screen.getByText(/改这里不生效/)).toBeTruthy()
    // The timeout is not env-owned, so it stays editable.
    expect((screen.getByLabelText('检索超时秒数') as HTMLInputElement).disabled).toBe(false)
  })

  it('测试连接用未保存的 Key 真发一次检索，并把工具的原话显示出来', async () => {
    testWebSearch.mockResolvedValue({ ok: false, message: '错误：联网检索失败（HTTP 401 — bad key）。' })
    await ready()

    fireEvent.change(screen.getByLabelText('联网检索密钥'), { target: { value: 'tvly-bad' } })
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))

    await waitFor(() =>
      expect(testWebSearch).toHaveBeenCalledWith({
        api_key: 'tvly-bad',
        search_url: 'https://api.tavily.com',
      }),
    )
    await waitFor(() => expect(screen.getByText(/HTTP 401/)).toBeTruthy())
  })

  it('保存失败把服务端的原话显示出来', async () => {
    saveWebSettings.mockRejectedValue(new Error('检索端点是接口根地址，要带协议头（http:// 或 https://）：x'))
    await ready()

    fireEvent.change(screen.getByLabelText('检索接口根地址'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: '保存联网设置' }))

    await waitFor(() => expect(screen.getByText(/要带协议头/)).toBeTruthy())
  })
})
