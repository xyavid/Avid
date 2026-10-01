// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Composer } from '../Composer'

afterEach(cleanup)

describe('Composer（参考图输入区）', () => {
  it('裸输入框就位；发送钮禁用并注明阶段 5 接线', () => {
    render(<Composer permission="manual" onChangePermission={() => {}} />)

    const input = screen.getByPlaceholderText('给 Avid 发消息…') as HTMLInputElement
    expect(input.disabled).toBe(true)

    const send = screen.getByRole('button', { name: '发送' }) as HTMLButtonElement
    expect(send.disabled).toBe(true)
    expect(send.className).toContain('bg-accent')
    expect(send.title).toContain('阶段 5')
  })

  it('权限按钮在图标行里，chip 反映当前模式', () => {
    render(<Composer permission="auto" onChangePermission={() => {}} />)

    expect(screen.getByRole('button', { name: '附加' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '附件' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '发送' })).toBeTruthy()
    expect(screen.getByText('自动')).toBeTruthy()
  })

  it('权限胶囊可打开模式卡片（可交互，不受发送禁用影响）', () => {
    render(<Composer permission="manual" onChangePermission={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: /权限模式/ }))
    const dialog = screen.getByRole('dialog', { name: '权限模式' })
    expect(dialog.textContent).toContain('手动')
    expect(dialog.textContent).toContain('自动')
    expect(dialog.textContent).toContain('完全')
  })
})
