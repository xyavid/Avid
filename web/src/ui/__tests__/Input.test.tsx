// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Input } from '../Input'

describe('Input（组件墙 §输入框）', () => {
  afterEach(cleanup)

  it('受控输入：输入值回传 onChange', () => {
    const onChange = vi.fn()
    render(<Input value="" onChange={onChange} placeholder="常态 · 高度 34px" />)

    const el = screen.getByPlaceholderText('常态 · 高度 34px') as HTMLInputElement
    fireEvent.change(el, { target: { value: '你好' } })
    expect(onChange).toHaveBeenCalledOnce()
  })

  it('placeholder 直通渲染', () => {
    render(<Input value="" onChange={() => {}} placeholder="给 hana 发消息..." />)

    expect(screen.getByPlaceholderText('给 hana 发消息...')).toBeTruthy()
  })

  it('禁用态：disabled 属性生效（jsdom 的 fireEvent 会绕过禁用派发，行为层只验属性）', () => {
    render(
      <Input value="" onChange={() => {}} disabled placeholder="禁用" />,
    )

    expect((screen.getByPlaceholderText('禁用') as HTMLInputElement).disabled).toBe(true)
  })

  it('focus 光晕由类提供（gallery 用它摆拍 focus 态）', () => {
    render(<Input value="" onChange={() => {}} />)

    expect(screen.getByRole('textbox').className).toContain('shadow-focus-ring')
  })

  it('bare 变体：无描边无底色无固定高（composer 壳内使用）', () => {
    render(<Input bare value="" onChange={() => {}} placeholder="裸" />)

    const classes = (screen.getByPlaceholderText('裸') as HTMLInputElement).className.split(' ')
    expect(classes).not.toContain('border-hair')
    expect(classes).not.toContain('bg-card')
    expect(classes).not.toContain('h-control')
  })
})
