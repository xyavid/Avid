// @vitest-environment jsdom
/**
 * `full` 的二次确认（阶段 26）。
 *
 * 要证明的是那条产品规则在 UI 上真的成立：**选中 full 不等于得到 full**——
 * 只有按下确认按钮才会把值交出去，关掉弹窗什么都不发生。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ReactNode } from 'react'

import { LocaleProvider } from '../../../lib/i18n'
import { PermissionSelector } from '../components/PermissionSelector'

function withLocale(node: ReactNode) {
  return render(<LocaleProvider>{node}</LocaleProvider>)
}

/** 选择器里的 `<select aria-label="权限模式">`。 */
function select(): HTMLSelectElement {
  return screen.getByLabelText('权限模式') as HTMLSelectElement
}

afterEach(cleanup)

describe('权限模式选择器', () => {
  it('三档都在，缺省不回落到 full', () => {
    withLocale(<PermissionSelector value="manual" onChange={() => undefined} />)
    expect(Array.from(select().options).map((option) => option.value)).toEqual([
      'manual',
      'auto',
      'full',
    ])
  })

  it('选 manual / auto 直接生效，不弹确认', () => {
    const onChange = vi.fn()
    withLocale(<PermissionSelector value="manual" onChange={onChange} />)

    fireEvent.change(select(), { target: { value: 'auto' } })

    expect(onChange).toHaveBeenCalledWith('auto')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('选 full 先弹确认，取消则什么都不发生', () => {
    const onChange = vi.fn()
    withLocale(<PermissionSelector value="manual" onChange={onChange} />)

    fireEvent.change(select(), { target: { value: 'full' } })

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText('确认关闭沙箱？')).toBeTruthy()

    fireEvent.click(screen.getByText('取消'))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('确认之后才把 full 交出去', () => {
    const onChange = vi.fn()
    withLocale(<PermissionSelector value="manual" onChange={onChange} />)

    fireEvent.change(select(), { target: { value: 'full' } })
    fireEvent.click(screen.getByText('我明白，关闭沙箱'))

    expect(onChange).toHaveBeenCalledWith('full')
  })
})
