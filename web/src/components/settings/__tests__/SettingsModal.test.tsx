// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SettingsModal } from '../SettingsModal'

const getModelSettings = vi.fn()
const saveModelSettings = vi.fn()
const resetModelSettings = vi.fn()

vi.mock('../../../api/client', () => ({
  getModelSettings: (...a: unknown[]) => getModelSettings(...a),
  saveModelSettings: (...a: unknown[]) => saveModelSettings(...a),
  resetModelSettings: (...a: unknown[]) => resetModelSettings(...a),
}))

const CURRENT = {
  model: 'deepseek/deepseek-v4.1-flash',
  base_url: 'https://env.example/v1',
  provider: null,
  api_key_set: true,
  overlay_active: false,
}

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  getModelSettings.mockReset().mockResolvedValue(CURRENT)
  saveModelSettings.mockReset().mockResolvedValue({ ...CURRENT, model: 'ui-model', overlay_active: true })
  resetModelSettings.mockReset().mockResolvedValue(undefined)
})
afterEach(cleanup)

describe('SettingsModal（设置界面）', () => {
  it('模型表单：载入生效值；密钥只入不出（password 空、已配置提示）', async () => {
    render(<SettingsModal onClose={() => {}} />)

    await waitFor(() => expect(screen.getByDisplayValue('deepseek/deepseek-v4.1-flash')).toBeTruthy())
    expect(screen.getByDisplayValue('https://env.example/v1')).toBeTruthy()

    const key = screen.getByPlaceholderText('已配置 · 输入新值以替换') as HTMLInputElement
    expect(key.type).toBe('password')
    expect(key.value).toBe('')
    expect(screen.getByText('当前来自 .env')).toBeTruthy()
  })

  it('保存：PUT 带 trim 后的字段；密钥为空时不发送该字段', async () => {
    render(<SettingsModal onClose={() => {}} />)
    await waitFor(() => expect(getModelSettings).toHaveBeenCalled())

    fireEvent.change(screen.getByPlaceholderText('中转站 / 自部署服务的接口地址，以 /v1 结尾'), {
      target: { value: ' https://ui.example/v1 ' },
    })
    fireEvent.change(screen.getByPlaceholderText('例如 deepseek-chat'), { target: { value: ' ui-model ' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(saveModelSettings).toHaveBeenCalledOnce())
    expect(saveModelSettings).toHaveBeenCalledWith({
      base_url: 'https://ui.example/v1',
      provider: '',
      model: 'ui-model',
    })
    await waitFor(() => expect(screen.getByText(/已保存/)).toBeTruthy())
  })

  it('输入新密钥后保存会带上 api_key', async () => {
    render(<SettingsModal onClose={() => {}} />)
    await waitFor(() => expect(getModelSettings).toHaveBeenCalled())

    fireEvent.change(screen.getByPlaceholderText('已配置 · 输入新值以替换'), { target: { value: 'sk-new' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(saveModelSettings).toHaveBeenCalledWith(expect.objectContaining({ api_key: 'sk-new' })),
    )
  })

  it('重置：撤销界面配置并重新拉取生效值', async () => {
    render(<SettingsModal onClose={() => {}} />)
    await waitFor(() => expect(getModelSettings).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: '重置' }))

    await waitFor(() => expect(resetModelSettings).toHaveBeenCalledOnce())
    await waitFor(() => expect(screen.getByText(/已清除界面配置/)).toBeTruthy())
  })

  it('协议下拉：四个选项（自动识别 / OpenAI / Anthropic / Gemini）', async () => {
    render(<SettingsModal onClose={() => {}} />)
    await waitFor(() => expect(getModelSettings).toHaveBeenCalled())

    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).toEqual(['', 'openai', 'anthropic', 'gemini'])
  })

  it('外观段：模式切到深色 → data-theme=midnight', async () => {
    render(<SettingsModal onClose={() => {}} />)

    fireEvent.click(screen.getByRole('tab', { name: '深色' }))
    expect(document.documentElement.dataset.theme).toBe('midnight')
  })

  it('关闭：✕ / Esc / 遮罩三条路都回调 onClose', async () => {
    const onClose = vi.fn()
    const { container } = render(<SettingsModal onClose={onClose} />)

    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(1)

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)

    const backdrop = container.querySelector('[aria-hidden="true"]') as HTMLElement
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledTimes(3)
  })
})
