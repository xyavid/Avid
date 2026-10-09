// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Composer } from '../Composer'

afterEach(cleanup)

function renderComposer(overrides?: { busy?: boolean; disabled?: boolean; model?: string | null }) {
  const onSend = vi.fn()
  const onStop = vi.fn()
  const view = render(
    <Composer
      full={false}
      onToggleFull={() => {}}
      // 阶段 54 起模型是必选：默认给一个，专测"没选"的那条用例自己覆盖成 null
      model="stub/a"
      onChangeModel={() => {}}
      onSend={onSend}
      onStop={onStop}
      {...overrides}
    />,
  )
  return { onSend, onStop, ...view }
}

const input = () => screen.getByPlaceholderText(/说点什么…/) as HTMLTextAreaElement

describe('Composer（多行输入与发送/停止）', () => {
  it('发送/停止是带文字的按钮（不是只有图标），文字就是它的可访问名', () => {
    const { rerender } = render(<Composer full={false} onToggleFull={() => {}} onSend={() => {}} onStop={() => {}} />)

    expect(screen.getByRole('button', { name: '发送' }).textContent).toContain('发送')

    rerender(<Composer full={false} onToggleFull={() => {}} busy onSend={() => {}} onStop={() => {}} />)
    expect(screen.getByRole('button', { name: '停止' }).textContent).toContain('停止')
  })

  it('输入后发送钮解禁；点击回调带 trim 后文本并清空输入', () => {
    const { onSend } = renderComposer()

    const send = () => screen.getByRole('button', { name: '发送' })
    expect((send() as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(input(), { target: { value: '  跑一下  ' } })
    expect((send() as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(send())
    expect(onSend).toHaveBeenCalledWith('跑一下', [])
    expect(input().value).toBe('')
  })

  it('没选模型不发车：发送钮保持禁用（不由服务端替用户决定用哪个模型）', () => {
    renderComposer({ model: null })

    fireEvent.change(input(), { target: { value: '跑一下' } })

    expect((screen.getByRole('button', { name: '发送' }) as HTMLButtonElement).disabled).toBe(true)
    // 胶囊此时是"选择模型"，并把原因写在可访问名里（不靠说明句）
    expect(screen.getByRole('button', { name: /模型/ }).textContent).toContain('选择模型')
  })

  it('Enter 发送；空文本 Enter 不触发', () => {
    const { onSend } = renderComposer()

    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()

    fireEvent.change(input(), { target: { value: '跑一下' } })
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith('跑一下', [])
  })

  it('Shift+Enter 换行不发送；IME 合成中的 Enter 不发送，合成结束才恢复', () => {
    const { onSend } = renderComposer()
    const area = input()

    fireEvent.change(area, { target: { value: '第一行' } })
    fireEvent.keyDown(area, { key: 'Enter', shiftKey: true })
    expect(onSend).not.toHaveBeenCalled()

    fireEvent.compositionStart(area)
    fireEvent.keyDown(area, { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()

    fireEvent.compositionEnd(area)
    fireEvent.keyDown(area, { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith('第一行', [])
  })

  it('busy：输入仍可编辑（先写好下一条），发送钮变停止钮，点它回调 onStop', () => {
    const { onStop, onSend } = renderComposer({ busy: true })

    const area = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(area.disabled).toBe(false)
    fireEvent.change(area, { target: { value: '下一条' } })
    expect(area.value).toBe('下一条')
    fireEvent.keyDown(area, { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '停止' }))
    expect(onStop).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: '发送' })).toBeNull()
  })

  it('disabled（无选中会话）：输入禁用；装饰性的「附加/附件」死按钮不再渲染', () => {
    renderComposer({ disabled: true })

    expect(input().disabled).toBe(true)
    expect(screen.queryByRole('button', { name: '附加' })).toBeNull()
    expect(screen.queryByRole('button', { name: '附件' })).toBeNull()
  })

  it('权限胶囊仍可交互（不受 busy/disabled 影响）', () => {
    renderComposer({ disabled: true })

    fireEvent.click(screen.getByRole('button', { name: /权限/ }))
    expect(screen.getByRole('dialog', { name: '权限' })).toBeTruthy()
  })
})

describe('Composer 的图片草稿（阶段 59）', () => {
  const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 1, 2, 3])

  beforeEach(() => {
    // jsdom 没有 object URL：给一个可控替身，断言只看 chip 有没有出现。
    Object.assign(URL, {
      createObjectURL: () => 'blob:test',
      revokeObjectURL: () => undefined,
    })
  })

  function pasteImage() {
    const file = new File([PNG], 'shot.png', { type: 'image/png' })
    fireEvent.paste(input(), { clipboardData: { files: [file] } })
  }

  it('粘贴图片出 chip，随后可以随文本一起发出去', async () => {
    const { onSend } = renderComposer()
    pasteImage()

    expect(await screen.findByText('shot.png')).toBeTruthy()
    fireEvent.change(input(), { target: { value: '看这个' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(onSend).toHaveBeenCalledTimes(1)
    const [text, images] = onSend.mock.calls[0]!
    expect(text).toBe('看这个')
    expect(images).toHaveLength(1)
    expect(images[0].name).toBe('shot.png')
    expect(images[0].data).toBe(btoa(String.fromCharCode(...PNG)))
  })

  it('只有图没有文字也能发（那张图本身就是要说的话）', async () => {
    const { onSend } = renderComposer()
    pasteImage()

    expect(await screen.findByText('shot.png')).toBeTruthy()
    const send = screen.getByRole('button', { name: '发送' }) as HTMLButtonElement
    expect(send.disabled).toBe(false)
    fireEvent.click(send)

    const [text, images] = onSend.mock.calls[0]!
    expect(text).toBe('')
    expect(images).toHaveLength(1)
  })

  it('非图片文件被挡下并给出原因', async () => {
    renderComposer()
    const file = new File([new Uint8Array([1, 2, 3])], 'a.pdf', { type: 'application/pdf' })
    fireEvent.paste(input(), { clipboardData: { files: [file] } })

    expect(await screen.findByRole('alert')).toHaveProperty('textContent', '只收图片（png / jpeg / webp / gif）')
  })

  it('移除 chip 后按钮又禁用（没有内容可发）', async () => {
    renderComposer()
    pasteImage()

    fireEvent.click(await screen.findByRole('button', { name: '移除 shot.png' }))
    const send = screen.getByRole('button', { name: '发送' }) as HTMLButtonElement
    expect(send.disabled).toBe(true)
  })
})
