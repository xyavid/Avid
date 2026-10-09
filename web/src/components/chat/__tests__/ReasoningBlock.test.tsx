// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ReasoningBlock } from '../ReasoningBlock'

/**
 * The block exists only in the stream (`reasoning_delta` is not persisted): expanded while
 * streaming, folded to one line when the run ends, and still expandable for review.
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
    // Folded: the body is not part of the accessible content.
    expect(screen.queryByText(/想完了/)).toBeNull()
  })

  it('收尾后折叠行给出持续时长（首末增量的时间差）', () => {
    render(<ReasoningBlock text="想了三秒" streaming={false} durationMs={3200} />)

    expect(screen.getByText('思考 · 3.2s')).toBeTruthy()
    // Without a reading the row says "done" only; no duration is invented.
    cleanup()
    render(<ReasoningBlock text="想了三秒" streaming={false} />)
    expect(screen.getByText('思考完成')).toBeTruthy()
  })

  it('收尾后仍可点开回看', () => {
    render(<ReasoningBlock text="想完了" streaming={false} />)

    fireEvent.click(screen.getByRole('button', { name: /思考完成/ }))
    expect(screen.getByText(/想完了/)).toBeTruthy()
    // A second click folds it back.
    fireEvent.click(screen.getByRole('button', { name: /思考完成/ }))
    expect(screen.queryByText(/想完了/)).toBeNull()
  })
})
