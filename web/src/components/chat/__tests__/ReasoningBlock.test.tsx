// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ReasoningBlock } from '../ReasoningBlock'

/**
 * 思考块：内核在流里发 `reasoning_delta`（不落盘），所以这个块**只在流里存在**——
 * 流式时展开、收尾自动折成一行，之后还能点开回看。
 */
describe('思考块', () => {
  afterEach(cleanup)

  it('没有思考内容时整块不渲染（大多数模型不吐 reasoning）', () => {
    const { container } = render(<ReasoningBlock text="" streaming />)

    expect(container.textContent).toBe('')
  })

  it('流式中：展开、标题是「思考中…」、正文可见', () => {
    render(<ReasoningBlock text="先看 checkpoint 的五层…" streaming />)

    expect(screen.getByText('思考中…')).toBeTruthy()
    expect(screen.getByText(/先看 checkpoint 的五层/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /思考中/ }).getAttribute('aria-expanded')).toBe('true')
  })

  it('收尾自动折起，标题变「思考完成」，正文收进折叠里', () => {
    const { rerender } = render(<ReasoningBlock text="想完了" streaming />)
    rerender(<ReasoningBlock text="想完了" streaming={false} />)

    expect(screen.getByText('思考完成')).toBeTruthy()
    expect(screen.getByRole('button', { name: /思考完成/ }).getAttribute('aria-expanded')).toBe('false')
    // 折起后正文不在可访问内容里
    expect(screen.queryByText(/想完了/)).toBeNull()
  })

  it('收尾后仍可点开回看', () => {
    render(<ReasoningBlock text="想完了" streaming={false} />)

    fireEvent.click(screen.getByRole('button', { name: /思考完成/ }))
    expect(screen.getByText(/想完了/)).toBeTruthy()
    // 再点一次收回去
    fireEvent.click(screen.getByRole('button', { name: /思考完成/ }))
    expect(screen.queryByText(/想完了/)).toBeNull()
  })
})
