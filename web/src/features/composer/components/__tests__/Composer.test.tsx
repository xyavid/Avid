// @vitest-environment jsdom
/**
 * Composer 的用例。
 *
 * 覆盖的是**守卫**，不是像素：提交的四个前置条件（非空 / 非 disabled / 非 running /
 * 非提交中）、Enter 与 Shift+Enter 的分工、以及切到 `full` 必须过确认弹窗。
 * 最后一条是安全关键——它对应 `full_access_ack` 那个"有意识的动作"，
 * 如果哪天有人把确认弹窗当成多余的一步删掉，这里必须红。
 *
 * `afterEach(cleanup)` 不能省：vitest 没开 globals，Testing Library 的自动清理不会注册，
 * 两次 render 的 DOM 会叠在一起，"按名字查按钮"就会命中多个节点。
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Composer } from '../Composer'
import type { ComposerProps } from '../Composer'

afterEach(cleanup)

const BASE: ComposerProps = {
  disabled: false,
  running: false,
  phase: 'idle',
  permission: 'manual',
  onPermissionChange: () => {},
  sandbox: null,
  usage: null,
  onSubmit: () => {},
  onCancel: () => {},
}

function renderComposer(overrides: Partial<ComposerProps> = {}): void {
  render(<Composer {...BASE} placeholder="输入" {...overrides} />)
}

function type(value: string): HTMLElement {
  const textarea = screen.getByLabelText('输入')
  fireEvent.change(textarea, { target: { value } })
  return textarea
}

describe('Composer 提交守卫', () => {
  it('Enter 提交，带上 trim 后的 prompt 与当前模式', () => {
    const onSubmit = vi.fn()
    renderComposer({ onSubmit })

    const textarea = type('  你好  ')
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith({ prompt: '你好', mode: 'manual' })
  })

  it('Shift+Enter 不提交（换行由 textarea 自己处理）', () => {
    const onSubmit = vi.fn()
    renderComposer({ onSubmit })

    const textarea = type('第一行')
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true })

    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('只有空白不提交', () => {
    const onSubmit = vi.fn()
    renderComposer({ onSubmit })

    const textarea = type('   \n  ')
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('running 时 Enter 不提交', () => {
    const onSubmit = vi.fn()
    renderComposer({ onSubmit, running: true })

    const textarea = type('再跑一次')
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('disabled 时不可提交', () => {
    const onSubmit = vi.fn()
    renderComposer({ onSubmit, disabled: true })

    const textarea = type('没有会话')
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('提交中按钮进入 loading 并禁用，重复 Enter 不会再发一次；成功后草稿被消费', async () => {
    const onSubmit = vi.fn(async () => {})
    renderComposer({ onSubmit })

    const textarea = type('跑吧')
    fireEvent.keyDown(textarea, { key: 'Enter' })

    const send = screen.getByRole('button', { name: '发送' }) as HTMLButtonElement
    expect(send.disabled).toBe(true)
    expect(send.getAttribute('aria-busy')).toBe('true')

    // 立刻再按一次：Promises 还没结算，守卫应当把这一下挡住。
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)

    // 结算后 loading 结束，草稿被清掉（空草稿下按钮自然回到禁用）。
    await waitFor(() => expect(send.getAttribute('aria-busy')).toBeNull())
    expect((screen.getByLabelText('输入') as HTMLTextAreaElement).value).toBe('')
  })

  it('提交失败时草稿必须还在', async () => {
    // 调用方（useRunStream）起运行失败会往上抛；用户不该因此重打一遍。
    const onSubmit = vi.fn(async () => {
      throw new Error('网络断了')
    })
    renderComposer({ onSubmit })

    const textarea = type('这段不能丢')
    fireEvent.keyDown(textarea, { key: 'Enter' })

    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: '发送' }) as HTMLButtonElement).getAttribute(
          'aria-busy',
        ),
      ).toBeNull(),
    )
    expect((screen.getByLabelText('输入') as HTMLTextAreaElement).value).toBe('这段不能丢')
  })

  it('onSubmit 返回 void（没有失败通道）时按已受理清空', () => {
    const onSubmit = vi.fn()
    renderComposer({ onSubmit })

    // 故意带首尾空白：提交的是 trim 后的 prompt，但清空要比对未 trim 的原文。
    const textarea = type('  一次性  ') as HTMLTextAreaElement
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(onSubmit).toHaveBeenCalledWith({ prompt: '一次性', mode: 'manual' })
    expect(textarea.value).toBe('')
  })

  it('提交期间新打的字不会被成功的回调抹掉', async () => {
    let release: (() => void) | null = null
    const onSubmit = vi.fn(
      async () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    )
    renderComposer({ onSubmit })

    const textarea = type('第一条') as HTMLTextAreaElement
    fireEvent.keyDown(textarea, { key: 'Enter' })
    // 用户在上一条还没落地时就开始写第二条。
    fireEvent.change(textarea, { target: { value: '第二条' } })

    await act(async () => {
      release?.()
    })

    expect(textarea.value).toBe('第二条')
  })

  it('运行中把发送换成停止，发送按钮不再存在', () => {
    renderComposer({ running: true })

    expect(screen.getByRole('button', { name: '停止' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '发送' })).toBeNull()
  })

  it('点停止走 onCancel', () => {
    const onCancel = vi.fn()
    renderComposer({ running: true, onCancel })

    fireEvent.click(screen.getByRole('button', { name: '停止' }))

    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})

describe('Composer 键盘细节', () => {
  it('Esc 两段式：第一次只提示，第二次才清空', () => {
    renderComposer()

    const textarea = type('不想丢了这段') as HTMLTextAreaElement
    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(textarea.value).toBe('不想丢了这段')
    expect(screen.getByText(/再按一次 Esc/)).toBeTruthy()

    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(textarea.value).toBe('')
  })

  it('上膛后继续输入会解除，避免下一次 Esc 突然清空', () => {
    renderComposer()

    const textarea = type('abc') as HTMLTextAreaElement
    fireEvent.keyDown(textarea, { key: 'Escape' })
    fireEvent.change(textarea, { target: { value: 'abcd' } })
    fireEvent.keyDown(textarea, { key: 'Escape' })

    expect(textarea.value).toBe('abcd')
  })
})

describe('Composer 切到 full 的显式确认', () => {
  it('选「完全访问」先弹确认，确认后才通知父层', () => {
    const onPermissionChange = vi.fn()
    renderComposer({ onPermissionChange })

    fireEvent.click(screen.getByRole('button', { name: '手动' }))
    fireEvent.click(screen.getByRole('option', { name: /完全访问/ }))

    // 弹出确认之前，权限一点没动。
    expect(onPermissionChange).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText(/关掉沙箱与出网限制/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '我明白，切换' }))

    expect(onPermissionChange).toHaveBeenCalledWith('full')
  })

  it('取消确认则保持原模式', () => {
    const onPermissionChange = vi.fn()
    renderComposer({ onPermissionChange })

    fireEvent.click(screen.getByRole('button', { name: '手动' }))
    fireEvent.click(screen.getByRole('option', { name: /完全访问/ }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(onPermissionChange).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('切到 auto 不需要确认', () => {
    const onPermissionChange = vi.fn()
    renderComposer({ onPermissionChange })

    fireEvent.click(screen.getByRole('button', { name: '手动' }))
    fireEvent.click(screen.getByRole('option', { name: /自动/ }))

    expect(onPermissionChange).toHaveBeenCalledWith('auto')
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
