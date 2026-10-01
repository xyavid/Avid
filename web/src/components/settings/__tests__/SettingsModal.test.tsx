// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SettingsModal } from '../SettingsModal'

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})
afterEach(cleanup)

describe('SettingsModal（设置界面）', () => {
  it('模型段：显示当前模型与服务端配置出处', () => {
    render(<SettingsModal model="deepseek/deepseek-v4.1-flash" onClose={() => {}} />)

    expect(screen.getByText('模型')).toBeTruthy()
    expect(screen.getByText('deepseek/deepseek-v4.1-flash')).toBeTruthy()
    expect(screen.getByText(/AVID_MODEL/)).toBeTruthy()
  })

  it('模型未知时显示「—」', () => {
    render(<SettingsModal model={null} onClose={() => {}} />)

    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })

  it('外观段：模式切到深色 → data-theme=midnight；切浅色 → warm-paper', () => {
    render(<SettingsModal model="m" onClose={() => {}} />)

    fireEvent.click(screen.getByRole('tab', { name: '深色' }))
    expect(document.documentElement.dataset.theme).toBe('midnight')

    fireEvent.click(screen.getByRole('tab', { name: '浅色' }))
    expect(document.documentElement.dataset.theme).toBe('warm-paper')
  })

  it('主题卡：两套（暖纸/青夜），点青夜即深色', () => {
    render(<SettingsModal model="m" onClose={() => {}} />)

    expect(screen.getByText('暖纸')).toBeTruthy()
    expect(screen.getByText('青夜')).toBeTruthy()

    fireEvent.click(screen.getByText('青夜'))
    expect(document.documentElement.dataset.theme).toBe('midnight')
  })

  it('关闭：✕ / Esc / 遮罩点击三条路都回调 onClose', () => {
    const onClose = vi.fn()
    const { container } = render(<SettingsModal model="m" onClose={onClose} />)

    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(1)

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)

    const backdrop = container.querySelector('[aria-hidden="true"]') as HTMLElement
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledTimes(3)
  })
})
