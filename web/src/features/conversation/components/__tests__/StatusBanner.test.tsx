// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { StatusBanner } from '../StatusBanner'

afterEach(cleanup)

const ERROR = { code: 'model_timeout', message: '模型调用超时' }

describe('StatusBanner', () => {
  it('finished 且无错误、不 detached 时整条不渲染', () => {
    const { container } = render(
      <StatusBanner phase="finished" activity="" error={null} detached={false} />,
    )

    expect(container.innerHTML).toBe('')
  })

  it('idle 且无错误时不渲染', () => {
    const { container } = render(
      <StatusBanner phase="idle" activity="" error={null} detached={false} />,
    )

    expect(container.innerHTML).toBe('')
  })

  it('awaiting_approval 交给审批条，这里返回空（即使带 activity）', () => {
    const { container } = render(
      <StatusBanner phase="awaiting_approval" activity="等批准" error={null} detached={false} />,
    )

    expect(container.innerHTML).toBe('')
  })

  it('failed 显示错误消息、错误码，并让重试回调被调用', () => {
    const onRetry = vi.fn()
    render(
      <StatusBanner phase="failed" activity="" error={ERROR} detached={false} onRetry={onRetry} />,
    )

    expect(screen.getByText('模型调用超时')).toBeTruthy()
    expect(screen.getByText('model_timeout')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('failed 但没有 onRetry 时不出现重试按钮', () => {
    render(<StatusBanner phase="failed" activity="" error={ERROR} detached={false} />)

    expect(screen.queryByRole('button', { name: '重试' })).toBeNull()
  })

  it('failed 但 error 为 null 时仍然说「运行失败」', () => {
    render(<StatusBanner phase="failed" activity="" error={null} detached={false} />)

    expect(screen.getByText('运行失败')).toBeTruthy()
  })

  it('finished 带着错误时不能把错误吞掉', () => {
    render(<StatusBanner phase="finished" activity="" error={ERROR} detached={false} />)

    expect(screen.getByText('模型调用超时')).toBeTruthy()
  })

  it('cancelled 走 warn 档', () => {
    const { container } = render(
      <StatusBanner phase="cancelled" activity="" error={null} detached={false} />,
    )

    expect(screen.getByText('已取消')).toBeTruthy()
    expect(container.querySelector('.text-warn')).not.toBeNull()
  })

  it('running 显示阶段名与 activity，activity 为空时只显示阶段名', () => {
    const withActivity = render(
      <StatusBanner phase="running" activity="正在读取文件" error={null} detached={false} />,
    )
    expect(screen.getByText('正在运行')).toBeTruthy()
    expect(screen.getByText('正在读取文件')).toBeTruthy()
    expect(withActivity.container.querySelector('.animate-spin')).not.toBeNull()
    cleanup()

    render(<StatusBanner phase="running" activity="   " error={null} detached={false} />)
    expect(screen.getByText('正在运行')).toBeTruthy()
  })

  it('detached 追加一条提示（单独出现时也要渲染）', () => {
    render(<StatusBanner phase="finished" activity="" error={null} detached />)

    expect(screen.getByText('重连中，历史可能不完整')).toBeTruthy()
  })

  it('detached 与失败同时出现时两条都在', () => {
    render(<StatusBanner phase="failed" activity="" error={ERROR} detached />)

    expect(screen.getByText('模型调用超时')).toBeTruthy()
    expect(screen.getByText('重连中，历史可能不完整')).toBeTruthy()
  })
})
