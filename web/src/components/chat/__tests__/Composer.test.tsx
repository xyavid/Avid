// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Composer } from '../Composer'

afterEach(cleanup)

function renderComposer(overrides?: { busy?: boolean; disabled?: boolean }) {
  const onSend = vi.fn()
  const onStop = vi.fn()
  const view = render(
    <Composer
      permission="manual"
      onChangePermission={() => {}}
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
    const { rerender } = render(<Composer permission="manual" onChangePermission={() => {}} onSend={() => {}} onStop={() => {}} />)

    expect(screen.getByRole('button', { name: '发送' }).textContent).toContain('发送')

    rerender(<Composer permission="manual" onChangePermission={() => {}} busy onSend={() => {}} onStop={() => {}} />)
    expect(screen.getByRole('button', { name: '停止' }).textContent).toContain('停止')
  })

  it('输入后发送钮解禁；点击回调带 trim 后文本并清空输入', () => {
    const { onSend } = renderComposer()

    const send = () => screen.getByRole('button', { name: '发送' })
    expect((send() as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(input(), { target: { value: '  跑一下  ' } })
    expect((send() as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(send())
    expect(onSend).toHaveBeenCalledWith('跑一下')
    expect(input().value).toBe('')
  })

  it('Enter 发送；空文本 Enter 不触发', () => {
    const { onSend } = renderComposer()

    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()

    fireEvent.change(input(), { target: { value: '跑一下' } })
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith('跑一下')
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
    expect(onSend).toHaveBeenCalledWith('第一行')
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

    fireEvent.click(screen.getByRole('button', { name: /权限模式/ }))
    expect(screen.getByRole('dialog', { name: '权限模式' })).toBeTruthy()
  })
})
