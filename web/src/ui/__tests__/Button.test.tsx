// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Button } from '../Button'

describe('Button（组件墙 §按钮）', () => {
  afterEach(cleanup)

  it('描边为默认变体：渲染为 button，带描边类', () => {
    render(<Button>描边按钮</Button>)

    const el = screen.getByRole('button', { name: '描边按钮' })
    const classes = el.className.split(' ')
    expect(classes).toContain('border-hair')
    expect(classes).toContain('text-accent')
    expect(classes).not.toContain('bg-accent')
  })

  it('primary 变体带 accent 填充（主行动每屏 ≤1 是使用约定，不是类型约束）', () => {
    render(<Button variant="primary">主行动</Button>)

    expect(screen.getByRole('button').className).toContain('bg-accent')
  })

  it('点击回调生效', () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>点我</Button>)

    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('禁用态：不触发点击，带禁用样式', () => {
    const onClick = vi.fn()
    render(
      <Button disabled onClick={onClick}>
        禁用
      </Button>,
    )

    const el = screen.getByRole('button')
    expect(el.className).toContain('disabled:opacity-40')
    fireEvent.click(el)
    expect(onClick).not.toHaveBeenCalled()
  })
})
