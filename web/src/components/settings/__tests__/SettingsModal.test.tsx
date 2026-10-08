// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SettingsModal } from '../SettingsModal'
import type { ByokSettings } from '../../../api/types'

const getByokSettings = vi.fn()
const saveByokSettings = vi.fn()
const resetByokSettings = vi.fn()
const testByokModel = vi.fn()

vi.mock('../../../api/client', () => ({
  getByokSettings: (...a: unknown[]) => getByokSettings(...a),
  saveByokSettings: (...a: unknown[]) => saveByokSettings(...a),
  resetByokSettings: (...a: unknown[]) => resetByokSettings(...a),
  testByokModel: (...a: unknown[]) => testByokModel(...a),
}))

const EMPTY: ByokSettings = {
  providers: [],
  bindings: { chat: null },
}

const WIRED: ByokSettings = {
  providers: [
    {
      id: 'deepseek',
      label: 'DeepSeek',
      protocol: 'openai-compatible',
      base_url: 'https://api.deepseek.example/v1',
      headers: {},
      extra_body: {},
      enabled: true,
      models: [
        { id: 'deepseek-chat', label: null, context_window: 65536, max_output: null, capabilities: { tool_calling: true } },
      ],
      key_set: true,
    },
  ],
  bindings: { chat: 'deepseek/deepseek-chat' },
}

/** PUT 载荷 → 回显：key_set 按载荷里是否带 api_key 生成。 */
function savedState(input: unknown): ByokSettings {
  const providers = (input as { providers: Array<Record<string, unknown>> }).providers
  return {
    providers: providers.map((p) => ({ ...p, key_set: Boolean(p.api_key) })) as ByokSettings['providers'],
    bindings: { chat: 'deepseek/deepseek-chat' },
  }
}

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  getByokSettings.mockReset().mockResolvedValue(EMPTY)
  saveByokSettings.mockReset().mockImplementation(async (input: unknown) => savedState(input))
  resetByokSettings.mockReset().mockResolvedValue(undefined)
  testByokModel.mockReset().mockResolvedValue({
    ok: true,
    steps: [
      { step: 'chat', ok: true, detail: '密钥（如已配置）、端点与网络可用' },
      { step: 'tool', ok: true, detail: '模型正确返回了工具调用' },
    ],
  })
})
afterEach(cleanup)

describe('SettingsModal（设置界面 · BYOK 模型段）', () => {
  it('空配置：显示引导文案（BYOK 是唯一来源），不预填任何密钥', async () => {
    render(<SettingsModal onClose={() => {}} />)

    await waitFor(() => expect(screen.getByText(/还没有模型配置/)).toBeTruthy())
    expect(screen.getByRole('button', { name: '新增提供商' })).toBeTruthy()
    // 密钥输入框（若有）必须是 password 且不预填——只入不出
    const passwords = document.querySelectorAll('input[type="password"]')
    passwords.forEach((el) => expect((el as HTMLInputElement).value).toBe(''))
  })

  it('已配置：提供商卡片展示 key_set 与模型，chat 绑定下拉反映服务端值', async () => {
    getByokSettings.mockResolvedValue(WIRED)
    render(<SettingsModal onClose={() => {}} />)

    await waitFor(() => expect(screen.getByText('DeepSeek')).toBeTruthy())
    expect(screen.getByText('密钥已配置')).toBeTruthy()
    expect(screen.getByText('deepseek-chat')).toBeTruthy()
    const select = screen
      .getAllByRole('combobox')
      .find((el) => (el as HTMLSelectElement).value === 'deepseek/deepseek-chat')
    expect(select).toBeTruthy()
  })

  it('新增提供商：填表完成 → 进列表；总保存 → PUT 带完整载荷与绑定', async () => {
    render(<SettingsModal onClose={() => {}} />)
    await waitFor(() => expect(getByokSettings).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: '新增提供商' }))
    fireEvent.change(screen.getByPlaceholderText('deepseek'), { target: { value: 'deepseek' } })
    fireEvent.click(screen.getByRole('button', { name: '添加模型' }))
    fireEvent.change(screen.getByPlaceholderText('DeepSeek'), { target: { value: 'DeepSeek' } })
    fireEvent.change(screen.getByPlaceholderText('中转站 / 自部署服务的接口地址'), {
      target: { value: 'https://api.deepseek.example/v1' },
    })
    fireEvent.change(screen.getByPlaceholderText('输入 API 密钥（可留空）'), { target: { value: 'sk-new' } })
    fireEvent.change(screen.getByPlaceholderText('模型 id，例如 deepseek-chat'), {
      target: { value: 'deepseek-chat' },
    })
    fireEvent.click(screen.getByRole('button', { name: '完成' }))

    await waitFor(() => expect(screen.getByText('密钥已配置')).toBeTruthy())
    // 绑定 chat 槽位到刚加的模型，再整体保存
    const binding = screen.getAllByRole('combobox').find(
      (el) => (el as HTMLSelectElement).value === '',
    ) as HTMLSelectElement
    fireEvent.change(binding, { target: { value: 'deepseek/deepseek-chat' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(saveByokSettings).toHaveBeenCalledOnce())
    const payload = saveByokSettings.mock.calls[0]![0] as {
      providers: { id: string; api_key: string | null; models: { id: string }[] }[]
      bindings: { chat: string | null }
    }
    expect(payload.providers[0]!.api_key).toBe('sk-new') // 只入：随载荷发出
    expect(payload.providers[0]!.models[0]!.id).toBe('deepseek-chat')
    expect(payload.bindings.chat).toBe('deepseek/deepseek-chat')
    await waitFor(() => expect(screen.getByText(/已保存/)).toBeTruthy())
  })

  it('模型的推理强度与图片输入：两个选择都跟着载荷走（阶段 55）', async () => {
    render(<SettingsModal onClose={() => {}} />)
    await waitFor(() => expect(getByokSettings).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: '新增提供商' }))
    fireEvent.click(screen.getByRole('button', { name: '添加模型' }))
    fireEvent.change(screen.getByPlaceholderText('deepseek'), { target: { value: 'gw' } })
    fireEvent.change(screen.getByPlaceholderText('DeepSeek'), { target: { value: '网关' } })
    fireEvent.change(screen.getByPlaceholderText('中转站 / 自部署服务的接口地址'), {
      target: { value: 'https://gw.example/v1' },
    })
    fireEvent.change(screen.getByPlaceholderText('输入 API 密钥（可留空）'), { target: { value: 'sk-gw' } })
    fireEvent.change(screen.getByPlaceholderText('模型 id，例如 deepseek-chat'), {
      target: { value: 'reasoner' },
    })
    fireEvent.change(screen.getByLabelText('模型 reasoner 的推理强度'), { target: { value: 'high' } })
    fireEvent.change(screen.getByLabelText('模型 reasoner 的图片输入能力'), { target: { value: 'true' } })
    fireEvent.click(screen.getByRole('button', { name: '完成' }))

    await waitFor(() => expect(screen.getByText('密钥已配置')).toBeTruthy())
    const binding = screen.getAllByRole('combobox').find(
      (el) => (el as HTMLSelectElement).value === '',
    ) as HTMLSelectElement
    fireEvent.change(binding, { target: { value: 'gw/reasoner' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(saveByokSettings).toHaveBeenCalledOnce())
    const payload = saveByokSettings.mock.calls[0]![0] as {
      providers: { models: { id: string; reasoning_effort: string | null; capabilities: { vision: boolean | null } }[] }[]
    }
    expect(payload.providers[0]!.models[0]).toMatchObject({
      id: 'reasoner',
      reasoning_effort: 'high',
      capabilities: { vision: true },
    })
  })

  it('校验：接口地址缺协议时给出可执行的错误，不发起保存', async () => {
    render(<SettingsModal onClose={() => {}} />)
    await waitFor(() => expect(getByokSettings).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: '新增提供商' }))
    fireEvent.change(screen.getByPlaceholderText('deepseek'), { target: { value: 'x' } })
    fireEvent.change(screen.getByPlaceholderText('DeepSeek'), { target: { value: 'X' } })
    fireEvent.change(screen.getByPlaceholderText('中转站 / 自部署服务的接口地址'), {
      target: { value: 'api.example.com/v1' },
    })
    fireEvent.click(screen.getByRole('button', { name: '完成' }))

    expect(screen.getByText(/http\(s\):\/\//)).toBeTruthy()
    expect(saveByokSettings).not.toHaveBeenCalled()
  })

  it('测试连接：对当前表单值跑两步探测，逐步展示结果', async () => {
    getByokSettings.mockResolvedValue(WIRED)
    render(<SettingsModal onClose={() => {}} />)

    await waitFor(() => expect(screen.getByRole('button', { name: '测试' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '测试' }))

    await waitFor(() => expect(testByokModel).toHaveBeenCalledOnce())
    const [provider, modelId] = testByokModel.mock.calls[0]! as [Record<string, unknown>, string]
    expect(modelId).toBe('deepseek-chat')
    // 只入：api_key 为 null（未重填），服务端走已存密钥
    expect(provider.api_key).toBeNull()
    await waitFor(() => expect(screen.getByText(/最小对话/)).toBeTruthy())
    expect(screen.getByText(/工具冒烟/)).toBeTruthy()
  })

  it('重置：删掉 BYOK 配置与密钥并重新拉取', async () => {
    getByokSettings.mockResolvedValueOnce(WIRED).mockResolvedValueOnce(EMPTY)
    render(<SettingsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('DeepSeek')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: '重置' }))

    await waitFor(() => expect(resetByokSettings).toHaveBeenCalledOnce())
    await waitFor(() => expect(screen.getByText(/已清除 BYOK 配置与密钥/)).toBeTruthy())
  })

  it('chat 绑定切回「未绑定」时载荷为 null', async () => {
    getByokSettings.mockResolvedValue(WIRED)
    render(<SettingsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('DeepSeek')).toBeTruthy())

    const select = screen
      .getAllByRole('combobox')
      .find((el) => (el as HTMLSelectElement).value === 'deepseek/deepseek-chat') as HTMLSelectElement
    fireEvent.change(select, { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(saveByokSettings).toHaveBeenCalledOnce())
    const payload = saveByokSettings.mock.calls[0]![0] as { bindings: { chat: string | null } }
    expect(payload.bindings.chat).toBeNull()
  })

  it('外观段与关闭行为保持不变', async () => {
    const onClose = vi.fn()
    const { container } = render(<SettingsModal onClose={onClose} />)

    fireEvent.click(screen.getByRole('tab', { name: '深色' }))
    expect(document.documentElement.dataset.theme).toBe('midnight')

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)

    const backdrop = container.querySelector('[aria-hidden="true"]') as HTMLElement
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
